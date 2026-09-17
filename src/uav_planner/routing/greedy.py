"""Начальное решение задачи маршрутизации: жадное распределение галсов по
загрузке БВС (longest-processing-time — LPT) + разбиение на вылеты (Split) по
энергобюджету. Первая версия модуля ``routing`` — без OR-Tools, без учета
ветра и разворотов (это забота модуля ``motion``, пока не реализован);
скорость и бюджет передаются готовыми, здесь только геометрия и балансировка.

LPT — классическая эвристика минимизации времени выполнения (makespan) на
параллельных машинах: длинные галсы распределяются первыми, каждый — на
наименее загруженный на этот момент БВС. Не гарантирует оптимум (это задача
NP-трудная), но дает разумный результат «из коробки» для критерия «Время».

См. docs/trebovania/Математическая_модель.md, разделы 10-11.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Vehicle:
    """БВС-кандидат для распределения: скорость и бюджет времени уже готовы
    (см. ``fleet.flight_time_budget_s`` и учет ветра на вызывающей стороне)."""

    id: str
    speed_mps: float
    budget_s: float

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


@dataclass
class Sortie:
    """Один вылет: упорядоченный список галсов одного БВС и суммарное время съемки
    (без учета переходов между галсами — это добавляется на стороне расписания/карты)."""

    vehicle_id: str
    tracks: list[Track] = field(default_factory=list)
    flight_time_s: float = 0.0

    def add(self, track: Track, time_s: float) -> None:
        self.tracks.append(track)
        self.flight_time_s += time_s


@dataclass
class RoutingResult:
    sorties_by_vehicle: dict[str, list[Sortie]]
    unassigned_tracks: list[Track]


def greedy_assign_and_split(tracks: list[Track], vehicles: list[Vehicle]) -> RoutingResult:
    """Распределяет ``tracks`` между ``vehicles`` по LPT-эвристике и режет
    маршрут каждого БВС на вылеты по бюджету энергии (Split).

    Галс, который не помещается в бюджет ни одного БВС целиком (слишком длинный
    даже для одного вылета), возвращается в ``unassigned_tracks`` — признак
    невыполнимости (см. ПЛН.ФТ.10), а не ошибка.
    """
    if not vehicles:
        return RoutingResult(sorties_by_vehicle={}, unassigned_tracks=list(tracks))

    ordered_tracks = sorted(tracks, key=lambda t: t.length_m, reverse=True)

    load_s: dict[str, float] = {v.id: 0.0 for v in vehicles}
    open_sortie: dict[str, Sortie] = {}
    sorties_by_vehicle: dict[str, list[Sortie]] = {v.id: [] for v in vehicles}
    unassigned: list[Track] = []

    for track in ordered_tracks:
        candidates = [v for v in vehicles if track.length_m / v.speed_mps <= v.budget_s]
        if not candidates:
            unassigned.append(track)
            continue

        vehicle = min(candidates, key=lambda v: load_s[v.id])
        time_s = track.length_m / vehicle.speed_mps

        sortie = open_sortie.get(vehicle.id)
        if sortie is None or sortie.flight_time_s + time_s > vehicle.budget_s:
            sortie = Sortie(vehicle_id=vehicle.id)
            sorties_by_vehicle[vehicle.id].append(sortie)
            open_sortie[vehicle.id] = sortie

        sortie.add(track, time_s)
        load_s[vehicle.id] += time_s

    return RoutingResult(sorties_by_vehicle=sorties_by_vehicle, unassigned_tracks=unassigned)
