"""Маршрутизация галсов между БВС нескольких площадок — адаптация схемы
«взвешенный K-Means + m-TSP внутри кластера + балансировка узкого места»
под нашу задачу (min-max время выполнения — критерий J1, «Время»).

Шаг 1 (разбиение зоны на галсы) — не здесь, это ``coverage``. Здесь — три
следующих шага:

**Шаг 2. Взвешенная кластеризация.** В классическом K-Means центроиды на
каждой итерации пересчитываются как среднее точек кластера (Lloyd's
algorithm). Для нас это физически бессмысленно: центроид кластера — реальный
аэродром конкретного БВС (``Vehicle.launch_point``), самолет не может
«переехать» на середину своего участка. Поэтому центроиды здесь **не
двигаются** — это не итеративный Lloyd's, а взвешенное (штраф — стоимость
перелета от площадки) закрепление галса за ближайшим по стоимости БВС с
поправкой на его текущую загрузку, содержательно то же самое, что K-Means с
фиксированными центроидами (``_assign_to_nearest``). Если аэродромов меньше,
чем БВС, это работает само собой: у нас центроид на **экземпляр БВС**
(``Vehicle.launch_point``), а не на аэродром, так что несколько БВС с одного
аэродрома естественно дают несколько кластеров с общим центроидом — особый
случай не нужен.

**Шаг 3. Маршрут внутри кластера (m-TSP с фиксированным стартом).** Вместо
генетического алгоритма (DEAP) или Google OR-Tools — которые были бы
оправданы для сотен точек и жёстких ограничений — здесь эвристика «ближайший
сосед + 2-opt» (``_tsp_order``): для типичных сцен (десятки галсов на БВС)
она находит разумный, не обязательно оптимальный, тур за миллисекунды, без
новой тяжелой зависимости. У каждого галса, как и раньше, два допустимых
направления прохода — тур учитывает оба.

Фитнес-функция T_total = T_перелет_туда + T_галсы + T_перелет_обратно
оценивается здесь по прямой линии (см. v1-ограничение ниже) — это то же
приближение, которым Split ограничивал бюджет вылета и раньше.

**Шаг 4. Балансировка узкого места.** Разброс T_total между БВС (Т_max −
T_min) — это и есть надбавка к критерию J1 (min-max), которую можно снизить,
не меняя список галсов, а только их распределение. Пока разброс больше
``balance_epsilon_s``, «граничный» галс (последний в туре самого нагруженного
БВС — эвристически близкий к границе его области, не требующий точного
геометрического поиска границы) переносится наименее нагруженному БВС,
которому он вообще по силам соло, и оба тура перестраиваются. Итерации
ограничены (``max_balance_iterations``) — сходимость для произвольной
геометрии не гарантирована, но на практике несколько шагов заметно
выравнивают загрузку.

**Нюансы нашей задачи, из-за которых схема адаптирована, а не взята
дословно:**
  - БВС ограничен не только энергией «за раз», а вылетает несколько раз
    (замена АКБ) — кластер режется на вылеты по бюджету уже после
    балансировки (``_split_into_sorties``, бывший Split из ``greedy.py``),
    один и тот же алгоритм разбиения, что и раньше;
  - переходы на Шагах 2-4 оцениваются по прямой (Евклидово расстояние), без
    обхода бесполетных зон/препятствий — так же, как раньше в Split: честный
    обход строит ``visibility.find_path`` уже после маршрутизации
    (``api.plan_service._build_sortie_legs``), и по нему пересчитывается
    точный налет каждого вылета перед расписанием. Разворотов Дубинса нет —
    это забота нереализованного модуля ``motion``;
  - итоговый критерий J1 — это makespan **расписания** (со световым днем и
    многодневностью), а не просто сумма T_total по вылету; Шаг 4 балансирует
    по T_total (сумме прямолинейных оценок на БВС) как по сильной
    приближенной прокси J1, не пересчитывая расписание на каждой итерации —
    пересчитывать полное расписание (со световым днем) внутри цикла
    балансировки было бы дорого и в целом не нужно для выбора, ЧТО переносить.

См. docs/trebovania/Математическая_модель.md, разделы 10-11.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

DEFAULT_MAX_BALANCE_ITERATIONS = 6
DEFAULT_BALANCE_EPSILON_S = 90.0
_MAX_TWO_OPT_TRACKS = 80  # больше — тур строит только «ближайший сосед», без 2-opt (иначе O(n^3) дорого)


@dataclass(frozen=True)
class Vehicle:
    """БВС-кандидат для распределения: скорость и бюджет времени уже готовы
    (см. ``fleet.flight_time_budget_s`` и учет ветра на вызывающей стороне).
    ``launch_point`` — своя площадка вылета/посадки (UTM, центроид кластера
    на Шаге 2): у разных БВС парка она может быть разной (собственная
    локация экземпляра или ближайшая ВПП обстановки, см.
    ``api.plan_service._instance_launch_point``)."""

    id: str
    speed_mps: float
    budget_s: float
    launch_point: Point

    def __post_init__(self) -> None:
        if self.speed_mps <= 0:
            raise ValueError(f"speed_mps должен быть положительным, получено {self.speed_mps}")
        if self.budget_s <= 0:
            raise ValueError(f"budget_s должен быть положительным, получено {self.budget_s}")


@dataclass(frozen=True)
class Track:
    """Галс (или его часть после ``coverage.split_long_track``) — метры (UTM)."""

    id: str
    geometry: BaseGeometry  # LineString

    @property
    def length_m(self) -> float:
        return self.geometry.length

    @property
    def start_point(self) -> Point:
        return Point(self.geometry.coords[0])

    @property
    def end_point(self) -> Point:
        return Point(self.geometry.coords[-1])


@dataclass
class Sortie:
    """Один вылет: упорядоченный список галсов одного БВС и суммарное время
    в воздухе — галсы плюс переходы (площадка→первый галс, между галсами,
    последний галс→площадка), оцененные по прямой (см. docstring модуля)."""

    vehicle_id: str
    tracks: list[Track] = field(default_factory=list)
    flight_time_s: float = 0.0


@dataclass
class RoutingResult:
    sorties_by_vehicle: dict[str, list[Sortie]]
    unassigned_tracks: list[Track]


def _nearer(pos: Point, a: Point, b: Point) -> tuple[Point, Point]:
    """Возвращает (near, far) — какой конец галса ближе к ``pos``, по прямой."""
    return (a, b) if pos.distance(a) <= pos.distance(b) else (b, a)


def _solo_feasible(vehicle: Vehicle, track: Track) -> bool:
    """Может ли ``vehicle`` вообще выполнить ``track`` как единственный галс
    отдельного вылета (переход туда + галс + переход обратно ≤ бюджет)."""
    near, far = _nearer(vehicle.launch_point, track.start_point, track.end_point)
    solo_m = vehicle.launch_point.distance(near) + track.length_m + far.distance(vehicle.launch_point)
    return solo_m / vehicle.speed_mps <= vehicle.budget_s


# ---------- Шаг 2: взвешенная кластеризация (фиксированные центроиды) ----------

def _assign_to_nearest(tracks: list[Track], vehicles: list[Vehicle]) -> tuple[dict[str, list[Track]], list[Track]]:
    """Закрепляет каждый галс за БВС, минимизируя (стоимость перелета от
    площадки этого БВС) + (уже накопленная им нагрузка) — «штраф на
    перелет» из описания и балансировка в одном шаге. Длинные галсы
    обрабатываются первыми (тот же порядок, что и в прежней LPT-версии) —
    снижает риск, что длинный галс останется без места из-за более ранних
    решений по мелким."""
    accumulated: dict[str, float] = {v.id: 0.0 for v in vehicles}
    clusters: dict[str, list[Track]] = {v.id: [] for v in vehicles}
    unassigned: list[Track] = []

    for track in sorted(tracks, key=lambda t: t.length_m, reverse=True):
        candidates = [v for v in vehicles if _solo_feasible(v, track)]
        if not candidates:
            unassigned.append(track)
            continue

        def score(v: Vehicle) -> float:
            near, _ = _nearer(v.launch_point, track.start_point, track.end_point)
            transit_cost_s = v.launch_point.distance(near) / v.speed_mps
            return transit_cost_s + accumulated[v.id]

        best = min(candidates, key=score)
        clusters[best.id].append(track)
        near, _ = _nearer(best.launch_point, track.start_point, track.end_point)
        accumulated[best.id] += (best.launch_point.distance(near) + track.length_m) / best.speed_mps

    return clusters, unassigned


# ---------- Шаг 3: m-TSP внутри кластера (ближайший сосед + 2-opt) ----------

def _tour_cost_and_end(order: list[Track], start: Point) -> tuple[float, Point]:
    """Стоимость перелета+галсов по прямой без возврата, и точка окончания тура."""
    total = 0.0
    current = start
    for t in order:
        near, far = _nearer(current, t.start_point, t.end_point)
        total += current.distance(near) + t.length_m
        current = far
    return total, current


def _two_opt(order: list[Track], start: Point, max_passes: int = 20) -> list[Track]:
    if len(order) > _MAX_TWO_OPT_TRACKS:
        return order  # слишком много точек — оставляем чистого «ближайшего соседа»

    best_len, _ = _tour_cost_and_end(order, start)
    improved = True
    passes = 0
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                cand_len, _ = _tour_cost_and_end(candidate, start)
                if cand_len < best_len - 1e-6:
                    order = candidate
                    best_len = cand_len
                    improved = True
    return order


def _tsp_order(tracks: list[Track], start: Point) -> list[Track]:
    """Тур по галсам кластера с фиксированным стартом ``start`` (площадка
    БВС) — ближайший сосед, затем локальное улучшение 2-opt. Каждый галс
    проходится в том направлении, что ближе к текущей точке тура (как и при
    сборке итогового маршрута, см. ``api.plan_service._build_sortie_legs``)."""
    if len(tracks) <= 1:
        return list(tracks)

    remaining = list(tracks)
    route: list[Track] = []
    current = start
    while remaining:
        nxt = min(remaining, key=lambda t: min(current.distance(t.start_point), current.distance(t.end_point)))
        route.append(nxt)
        remaining.remove(nxt)
        _, far = _nearer(current, nxt.start_point, nxt.end_point)
        current = far

    return _two_opt(route, start)


# ---------- Шаг 4: балансировка узкого места ----------

def _cluster_total_cost_s(order: list[Track], vehicle: Vehicle) -> float:
    """T_total = переход туда + галсы + переход обратно, по прямой (та же
    оценка, что и в Split) — прокси для критерия J1 на Шаге 4."""
    if not order:
        return 0.0
    cost, end = _tour_cost_and_end(order, vehicle.launch_point)
    return (cost + end.distance(vehicle.launch_point)) / vehicle.speed_mps


def _balance_bottleneck(
    clusters: dict[str, list[Track]],
    vehicles: list[Vehicle],
    max_iterations: int,
    epsilon_s: float,
) -> dict[str, list[Track]]:
    by_id = {v.id: v for v in vehicles}
    costs = {vid: _cluster_total_cost_s(order, by_id[vid]) for vid, order in clusters.items()}

    for _ in range(max_iterations):
        if len(costs) < 2:
            break
        slow_id = max(costs, key=costs.get)
        fast_id = min(costs, key=costs.get)
        if costs[slow_id] - costs[fast_id] <= epsilon_s or not clusters[slow_id]:
            break

        slow_vehicle, fast_vehicle = by_id[slow_id], by_id[fast_id]
        # «Граничный» галс — последний в туре самого нагруженного БВС: в туре,
        # построенном от его площадки, это обычно самая дальняя точка —
        # дешевая эвристика границы кластера, без явного геометрического поиска.
        moved = clusters[slow_id][-1]
        if not _solo_feasible(fast_vehicle, moved):
            # Этому галсу быстрый БВС не по силам соло — переносить некуда,
            # дальше в этом направлении баланс не улучшить.
            break

        clusters[slow_id] = clusters[slow_id][:-1]
        clusters[fast_id] = clusters[fast_id] + [moved]
        clusters[slow_id] = _tsp_order(clusters[slow_id], slow_vehicle.launch_point)
        clusters[fast_id] = _tsp_order(clusters[fast_id], fast_vehicle.launch_point)

        costs[slow_id] = _cluster_total_cost_s(clusters[slow_id], slow_vehicle)
        costs[fast_id] = _cluster_total_cost_s(clusters[fast_id], fast_vehicle)

    return clusters


# ---------- Разбиение тура на вылеты по бюджету (бывший Split) ----------

def _split_into_sorties(order: list[Track], vehicle: Vehicle) -> tuple[list[Sortie], list[Track]]:
    """Режет уже упорядоченный (Шагами 2-4) список галсов одного БВС на
    вылеты по энергобюджету — тот же принцип, что и раньше: переход до
    следующего галса и зарезервированный обратный переход должны укладываться
    в остаток бюджета текущего вылета, иначе открывается новый."""
    sorties: list[Sortie] = []
    unassigned: list[Track] = []
    if not order:
        return sorties, unassigned

    current_sortie: Sortie | None = None
    position = vehicle.launch_point

    def close(sortie: Sortie, end_pos: Point) -> None:
        sortie.flight_time_s += end_pos.distance(vehicle.launch_point) / vehicle.speed_mps

    for track in order:
        near, far = _nearer(position, track.start_point, track.end_point)
        transit_in_s = position.distance(near) / vehicle.speed_mps
        survey_s = track.length_m / vehicle.speed_mps
        return_s = far.distance(vehicle.launch_point) / vehicle.speed_mps

        base_s = current_sortie.flight_time_s if current_sortie is not None else 0.0
        if current_sortie is None or base_s + transit_in_s + survey_s + return_s > vehicle.budget_s:
            if current_sortie is not None:
                close(current_sortie, position)
            if not _solo_feasible(vehicle, track):
                unassigned.append(track)
                continue
            current_sortie = Sortie(vehicle_id=vehicle.id)
            sorties.append(current_sortie)
            position = vehicle.launch_point
            near, far = _nearer(position, track.start_point, track.end_point)
            transit_in_s = position.distance(near) / vehicle.speed_mps

        current_sortie.tracks.append(track)
        current_sortie.flight_time_s += transit_in_s + survey_s
        position = far

    if current_sortie is not None:
        close(current_sortie, position)

    return sorties, unassigned


def cluster_assign_and_route(
    tracks: list[Track],
    vehicles: list[Vehicle],
    max_balance_iterations: int = DEFAULT_MAX_BALANCE_ITERATIONS,
    balance_epsilon_s: float = DEFAULT_BALANCE_EPSILON_S,
) -> RoutingResult:
    """Шаги 2-4: взвешенная кластеризация по площадкам БВС, TSP-тур внутри
    кластера, балансировка узкого места — затем разбиение каждого
    получившегося тура на вылеты по бюджету энергии (см. docstring модуля).

    Галс, который не помещается в бюджет ни одного БВС даже как единственный
    в отдельном вылете, возвращается в ``unassigned_tracks`` — признак
    невыполнимости (см. ПЛН.ФТ.10), а не ошибка.
    """
    if not vehicles:
        return RoutingResult(sorties_by_vehicle={}, unassigned_tracks=list(tracks))
    if not tracks:
        return RoutingResult(sorties_by_vehicle={v.id: [] for v in vehicles}, unassigned_tracks=[])

    clusters, unassigned = _assign_to_nearest(tracks, vehicles)
    clusters = {v.id: _tsp_order(clusters[v.id], v.launch_point) for v in vehicles}
    clusters = _balance_bottleneck(clusters, vehicles, max_balance_iterations, balance_epsilon_s)

    sorties_by_vehicle: dict[str, list[Sortie]] = {}
    for v in vehicles:
        sorties, leftover = _split_into_sorties(clusters[v.id], v)
        sorties_by_vehicle[v.id] = sorties
        unassigned.extend(leftover)

    return RoutingResult(sorties_by_vehicle=sorties_by_vehicle, unassigned_tracks=unassigned)
