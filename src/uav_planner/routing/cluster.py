"""Маршрутизация галсов между БВС нескольких площадок — адаптация схемы
«взвешенный K-Means + m-TSP внутри кластера + балансировка узкого места»
под нашу задачу (min-max время выполнения — критерий J1, «Время»).

Шаг 1 (разбиение зоны на галсы) — не здесь, это ``coverage``. Здесь — три
следующих шага:

**Шаг 2. Кластеризация по ближайшему центроиду.** В классическом K-Means
центроиды на каждой итерации пересчитываются как среднее точек кластера
(Lloyd's algorithm). Для нас это физически бессмысленно: центроид кластера —
реальный аэродром конкретного БВС (``Vehicle.launch_point``), самолет не
может «переехать» на середину своего участка. Поэтому центроиды здесь **не
двигаются**: каждый галс закрепляется (``_assign_to_nearest``) за бортом, чья
площадка ближе всего по времени перелёта — обычное разбиение на области
Вороного по стоимости перелёта, без поправки на текущую загрузку борта.
Загрузка учитывается только как разрешение явной ничьей (несколько
экземпляров на одной площадке, см. ``_TIE_TOLERANCE_S``) — иначе весь борт
уходил бы первому в списке дубликату. Раньше нагрузка была частью самого
критерия закрепления (штраф «+ уже накопленная бортом стоимость» к ЛЮБОЙ, не
только равной, стоимости перелёта, либо — в промежуточном варианте — рост
кластера от текущей позиции борта, а не от площадки): в обоих случаях борт,
оказавшийся в моменте обработки конкретного галса «дешевле» по нагрузке или
по текущей позиции, мог забрать пространственно далёкий от своих остальных
галсов кусок, случайно попавшийся дешёвым именно тогда — соседние по карте
галсы расходились по бортам «шахматкой», а первый галс нового вылета
выглядел «странно далёким». Разбиение по ближайшей ПЛОЩАДКЕ (без поправки на
загрузку, кроме точной ничьей) само по себе не даёт разным по расположению
кластерам перемешиваться; собственно балансировку нагрузки делает отдельно и
явно Шаг 4 (``_balance_bottleneck``) — переносом граничных галсов, а не
искажением самого разбиения. Если аэродромов меньше, чем БВС, это работает
само собой: у нас центроид на **экземпляр БВС** (``Vehicle.launch_point``), а
не на аэродром, так что несколько БВС с одного аэродрома естественно дают
несколько кластеров с общим центроидом — особый случай не нужен.

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

**Шаг 4. Балансировка узкого места.** J1 (min-max) определяет самый
загруженный борт, и его можно снизить, не меняя список галсов, а только их
распределение. Загрузка борта оценивается с разбиением тура на вылеты и
заменой АКБ между ними (``_vehicle_time_s``), а не длиной одного тура: тур
чуть длиннее бюджета — это лишний вылет и 15 минут простоя. Пока разброс
больше ``balance_epsilon_s``, «граничный» галс перегруженного борта (ближайший
по перелёту к площадке недогруженного) переносится недогруженному, если тому
он по силам соло, и оба тура перестраиваются. Одиночный перенос может и
ухудшить оценку — тур строит эвристика, — поэтому поиск не обрывается на
первой неудаче, а идет, пока ``DEFAULT_BALANCE_PATIENCE`` переносов подряд не
улучшат J1, и возвращает лучшее найденное распределение. Итерации ограничены
(``max_balance_iterations``) — сходимость для произвольной геометрии не
гарантирована.

**Нюансы нашей задачи, из-за которых схема адаптирована, а не взята
дословно:**
  - БВС ограничен не только энергией «за раз», а вылетает несколько раз
    (замена АКБ) — кластер режется на вылеты по бюджету уже после
    балансировки (``split_into_sorties``, бывший Split из ``greedy.py``),
    один и тот же алгоритм разбиения, что и раньше;
  - переходы на Шагах 2-4 оцениваются по прямой (Евклидово расстояние), без
    обхода бесполетных зон/препятствий — так же, как раньше в Split: честный
    обход строит ``visibility.find_path`` уже после маршрутизации
    (``api.plan_service._build_sortie_legs``), и по нему пересчитывается
    точный налет каждого вылета перед расписанием. Разворотов Дубинса нет —
    это забота нереализованного модуля ``motion``;
  - итоговый критерий J1 — это makespan **расписания** (со световым днем и
    многодневностью); Шаг 4 балансирует по ``_vehicle_time_s`` (налет
    вылетов по прямой плюс замена АКБ) как по приближенной прокси J1, не
    пересчитывая расписание на каждой итерации — полное расписание (со
    световым днем) внутри цикла балансировки было бы дорого и для выбора,
    ЧТО переносить, не нужно.

См. docs/trebovania/Математическая_модель.md, разделы 10-11.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

DEFAULT_MAX_BALANCE_ITERATIONS = 100  # цикл всё равно выходит раньше по epsilon/отсутствию выигрыша
                                       # (см. _balance_bottleneck) — граница нужна лишь на случай
                                       # больших сцен, где перебалансировка требует многих одиночных
                                       # переносов галса (иначе при 6 итерациях реальная сцена в
                                       # десятки галсов на борт не успевает выровняться до эпсилон)
DEFAULT_BALANCE_EPSILON_S = 90.0
# Сколько переносов подряд без улучшения J1 балансировка терпит, прежде чем
# сдаться: оценка тура — эвристика, и одиночный перенос может ее случайно
# ухудшить на десятки секунд, хотя следующие дают выигрыш в разы больше.
DEFAULT_BALANCE_PATIENCE = 10
# Накладные расходы между вылетами одного борта (замена АКБ) — то же
# допущение, что schedule.DEFAULT_OVERHEAD_S; число дублируется, потому что
# schedule сам импортирует этот модуль.
DEFAULT_SORTIE_OVERHEAD_S = 15 * 60.0
_MAX_TWO_OPT_TRACKS = 80  # больше — тур строит только «ближайший сосед», без 2-opt (иначе O(n^3) дорого)


@dataclass(frozen=True)
class Vehicle:
    """БВС-кандидат для распределения: скорость и бюджет времени уже готовы
    (см. ``fleet.flight_time_budget_s`` и учет ветра на вызывающей стороне).
    ``launch_point`` — своя площадка вылета/посадки (UTM, центроид кластера
    на Шаге 2): у разных БВС парка она может быть разной (собственная
    локация экземпляра или ближайшая ВПП обстановки, см.
    ``api.plan_service._instance_launch_point``).

    ``comm_range_m`` — дальность связи модели (``comm_range_km`` паспорта):
    галс, хоть одна точка которого дальше от площадки, борту не назначается.
    ``None`` — ограничения нет."""

    id: str
    speed_mps: float
    budget_s: float
    launch_point: Point
    comm_range_m: float | None = None

    def __post_init__(self) -> None:
        if self.speed_mps <= 0:
            raise ValueError(f"speed_mps должен быть положительным, получено {self.speed_mps}")
        if self.budget_s <= 0:
            raise ValueError(f"budget_s должен быть положительным, получено {self.budget_s}")
        if self.comm_range_m is not None and self.comm_range_m <= 0:
            raise ValueError(f"comm_range_m должен быть положительным, получено {self.comm_range_m}")


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


def _within_comm_range(vehicle: Vehicle, geometry: BaseGeometry) -> bool:
    """Весь галс в радиусе связи площадки ``vehicle``. Расстояние от точки
    вдоль отрезка выпукло, поэтому максимум — в одной из вершин ломаной:
    проверки вершин достаточно."""
    if vehicle.comm_range_m is None:
        return True
    limit = vehicle.comm_range_m + 1e-6
    return all(vehicle.launch_point.distance(Point(c)) <= limit for c in geometry.coords)


def _solo_feasible(vehicle: Vehicle, track: Track) -> bool:
    """Может ли ``vehicle`` вообще выполнить ``track`` как единственный галс
    отдельного вылета (переход туда + галс + переход обратно ≤ бюджет) и не
    выйти при этом из зоны связи."""
    if not _within_comm_range(vehicle, track.geometry):
        return False
    near, far = _nearer(vehicle.launch_point, track.start_point, track.end_point)
    solo_m = vehicle.launch_point.distance(near) + track.length_m + far.distance(vehicle.launch_point)
    return solo_m / vehicle.speed_mps <= vehicle.budget_s


def _transit_cost_s(vehicle: Vehicle, track: Track) -> float:
    """Время перелёта от площадки ``vehicle`` до ближайшего конца ``track``."""
    near, _ = _nearer(vehicle.launch_point, track.start_point, track.end_point)
    return vehicle.launch_point.distance(near) / vehicle.speed_mps


# ---------- Шаг 2: взвешенная кластеризация (фиксированные центроиды) ----------

_TIE_TOLERANCE_S = 1e-6  # разница в перелёте меньше этого — считаем «одна и та же площадка»


def _assign_to_nearest(tracks: list[Track], vehicles: list[Vehicle]) -> tuple[dict[str, list[Track]], list[Track]]:
    """Закрепляет каждый галс за бортом, чья площадка ближе всего по времени
    перелёта — разбиение по ближайшему (взвешенному по скорости) центроиду,
    без поправки на текущую загрузку, **кроме** явных ничьих: если несколько
    бортов вышли на одну и ту же минимальную стоимость перелёта (типично —
    несколько экземпляров на одном аэродроме, см. докстрочку модуля), выбор
    среди них по накопленной нагрузке — иначе всё подряд уходило бы только
    первому в списке борту, а остальные с той же площадки остались бы без
    ни одного галса до самой балансировки.

    Основную загрузку выравнивает отдельно и явно Шаг 4
    (``_balance_bottleneck``, ниже) — переносом граничных галсов от
    перегруженного борта к недогруженному. Раньше нагрузка учитывалась уже
    здесь, в самом закреплении (штраф «+ уже накопленная бортом нагрузка» к
    ЛЮБОЙ, не только равной, стоимости перелёта): из-за этого борт, который
    в конкретный момент обработки сцены оказывался «дешевле» по накопленной
    нагрузке, мог забрать пространственно далёкий от остальных своих галсов
    кусок, случайно попавшийся дешёвым именно в этот момент, — соседние по
    карте галсы расходились по разным бортам «шахматкой», а первый галс
    нового вылета выглядел «странно далёким». Разбиение по ближайшей
    площадке (с поправкой на загрузку только при точной ничьей) само по себе
    не даёт разным по расположению кластерам перемешиваться.
    """
    accumulated: dict[str, float] = {v.id: 0.0 for v in vehicles}
    clusters: dict[str, list[Track]] = {v.id: [] for v in vehicles}
    unassigned: list[Track] = []

    for track in tracks:
        candidates = [v for v in vehicles if _solo_feasible(v, track)]
        if not candidates:
            unassigned.append(track)
            continue

        best_cost = min(_transit_cost_s(v, track) for v in candidates)
        tied = [v for v in candidates if _transit_cost_s(v, track) <= best_cost + _TIE_TOLERANCE_S]
        best = min(tied, key=lambda v: accumulated[v.id])
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


def _nearest_neighbor_order(tracks: list[Track], start: Point) -> list[Track]:
    """Тур «ближайший сосед» без 2-opt — дешёвая O(n²) прикидка порядка,
    достаточная там, где важна не итоговая точность тура, а быстрая оценка
    стоимости (например, на каждой итерации Шага 4, где полировать тур
    2-opt'ом при каждом переносе одного галса было бы намного дороже, чем
    сам перенос)."""
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

    return route


def _tsp_order(tracks: list[Track], start: Point) -> list[Track]:
    """Тур по галсам кластера с фиксированным стартом ``start`` (площадка
    БВС) — ближайший сосед, затем локальное улучшение 2-opt. Каждый галс
    проходится в том направлении, что ближе к текущей точке тура (как и при
    сборке итогового маршрута, см. ``api.plan_service._build_sortie_legs``)."""
    return _two_opt(_nearest_neighbor_order(tracks, start), start)


# ---------- Шаг 4: балансировка узкого места ----------

def _vehicle_time_s(order: list[Track], vehicle: Vehicle, overhead_s: float) -> float:
    """Время работы борта по туру ``order`` с учетом разбиения на вылеты:
    налет всех вылетов плюс замена АКБ между ними — прокси J1 на Шаге 4.

    Длина одного непрерывного тура (как было раньше) для этого не годится: тур чуть длиннее бюджета режется на два вылета и добавляет
    ``overhead_s``, а по длине тура это не видно. Так балансировка оставляла
    борту 1927 с при бюджете 1920 — и лишний вылет с 15 минутами простоя.
    """
    if not order:
        return 0.0
    sorties, leftover = split_into_sorties(order, vehicle)
    if leftover:
        return float("inf")
    return sum(s.flight_time_s for s in sorties) + overhead_s * (len(sorties) - 1)


def _balance_bottleneck(
    clusters: dict[str, list[Track]],
    vehicles: list[Vehicle],
    max_iterations: int,
    epsilon_s: float,
    overhead_s: float = DEFAULT_SORTIE_OVERHEAD_S,
    patience: int = DEFAULT_BALANCE_PATIENCE,
) -> dict[str, list[Track]]:
    """Переносит граничные галсы от самого загруженного борта к наименее
    загруженному и возвращает лучшее найденное распределение по J1 (время
    самого загруженного борта), а не последнее.

    Раньше цикл обрывался на первом переносе, который не уменьшил разброс.
    Оценка тура — эвристика (ближайший сосед), и удаление галса из середины
    «змейки» иногда удлиняет оставшийся тур: на сцене в 41 галс второй
    перенос дал +20 с, балансировка остановилась, и один борт получил 40
    галсов (два вылета, J1 = 72 мин), а второй — один (8 мин). Теперь
    неудачный перенос не обрывает поиск: цикл идет дальше, пока ``patience``
    переносов подряд не улучшат J1.

    Получатель — не обязательно самый свободный борт: если ни один галс
    перегруженного ему не по силам (бюджет, дальность связи), пробуется
    следующий по загрузке, пока разброс с ним больше ``epsilon_s``. Раньше
    цикл в этом случае обрывался, хотя другой борт мог забрать галс.
    """
    by_id = {v.id: v for v in vehicles}
    costs = {vid: _vehicle_time_s(order, by_id[vid], overhead_s) for vid, order in clusters.items()}
    best_j1, best_clusters = max(costs.values(), default=0.0), dict(clusters)
    stale = 0

    for _ in range(max_iterations):
        if len(costs) < 2:
            break
        slow_id = max(costs, key=costs.get)
        if not clusters[slow_id]:
            break

        slow_vehicle = by_id[slow_id]
        move: tuple[str, Track] | None = None
        for fast_id in sorted(costs, key=costs.get):
            if fast_id == slow_id or costs[slow_id] - costs[fast_id] <= epsilon_s:
                break  # дальше по списку разброс только меньше
            fast_vehicle = by_id[fast_id]
            # «Граничный» галс — тот из кластера перегруженного борта, что
            # ближе всего по перелёту к площадке получателя: именно он лежит
            # на границе двух территорий. «Последний в собственном туре»
            # борта не годится — тур строится от ЕГО площадки, и дальняя
            # точка может быть где угодно, а не рядом с площадкой получателя:
            # так один перенос валил соседство кластеров, построенное Шагом 2,
            # в «шахматку». Галсы, которые получателю не по силам соло,
            # пропускаются — берется ближайший из выполнимых.
            feasible = [t for t in clusters[slow_id] if _solo_feasible(fast_vehicle, t)]
            if feasible:
                move = (fast_id, min(feasible, key=lambda t: _transit_cost_s(fast_vehicle, t)))
                break
        if move is None:
            break  # ни одному борту с заметно меньшей загрузкой нечего передать
        fast_id, moved = move
        fast_vehicle = by_id[fast_id]

        # Внутри цикла — только «ближайший сосед», без 2-opt: точный тур
        # строится один раз на итоговом распределении (см.
        # ``cluster_assign_and_route``), иначе балансировка на десятках
        # галсов на борт была бы дороже самого расчета.
        remaining = [t for t in clusters[slow_id] if t is not moved]
        clusters = dict(clusters)
        clusters[slow_id] = _nearest_neighbor_order(remaining, slow_vehicle.launch_point)
        clusters[fast_id] = _nearest_neighbor_order(clusters[fast_id] + [moved], fast_vehicle.launch_point)
        costs = dict(costs)
        costs[slow_id] = _vehicle_time_s(clusters[slow_id], slow_vehicle, overhead_s)
        costs[fast_id] = _vehicle_time_s(clusters[fast_id], fast_vehicle, overhead_s)

        if max(costs.values()) < best_j1 - 1e-9:
            best_j1, best_clusters = max(costs.values()), clusters
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break

    return best_clusters


# ---------- Разбиение тура на вылеты по бюджету (бывший Split) ----------

def split_into_sorties(order: list[Track], vehicle: Vehicle) -> tuple[list[Sortie], list[Track]]:
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
    sortie_overhead_s: float = DEFAULT_SORTIE_OVERHEAD_S,
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
    clusters = _balance_bottleneck(
        clusters, vehicles, max_balance_iterations, balance_epsilon_s, overhead_s=sortie_overhead_s
    )
    # Внутри балансировки тур строился дешёвым «ближайшим соседом» (без
    # 2-opt, см. докстрочку _balance_bottleneck) — здесь, один раз на
    # итоговом распределении, доводим каждый тур до локального оптимума.
    clusters = {v.id: _two_opt(clusters[v.id], v.launch_point) for v in vehicles}

    sorties_by_vehicle: dict[str, list[Sortie]] = {}
    for v in vehicles:
        sorties, leftover = split_into_sorties(clusters[v.id], v)
        sorties_by_vehicle[v.id] = sorties
        unassigned.extend(leftover)

    return RoutingResult(sorties_by_vehicle=sorties_by_vehicle, unassigned_tracks=unassigned)
