"""Тесты слоя хранения: то, что легко сломать при переносе данных в PostGIS.

Проверяют не бизнес-правила (их закрывают тесты API), а честность
преобразования: геометрия должна возвращаться той же, сырой GeoJSON — с теми
же свойствами, версии планов — расти, отчет проверки — не задваиваться.
"""

from datetime import date, datetime, time, timezone

from shapely.geometry import LineString, MultiLineString, Polygon, mapping, shape

from uav_planner import repositories
from uav_planner.api.schemas.environment import EnvironmentDetail, LayerCounts, ValidationIssue
from uav_planner.api.schemas.fleet import FleetDetail, FleetInstance
from uav_planner.api.schemas.plan import PlanDetail, PlanSortie
from uav_planner.api.schemas.safety import SafetyCheckOut, SafetyReport
from uav_planner.api.schemas.task import TaskDetail
from uav_planner.db.geo import from_db_geojson, to_db, to_db_multiline


def reload(db):
    """Сбросить кэш сессии, чтобы следующее чтение действительно пошло в БД.

    Без этого ``session.get()`` вернет тот же объект Python, что был записан, и
    тест проверит идентичность ссылки, а не сохранность данных.
    """
    db.expire_all()

NOW = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)

ENV_ID = "11111111-1111-1111-1111-111111111111"
TASK_ID = "22222222-2222-2222-2222-222222222222"
PLAN_ID = "33333333-3333-3333-3333-333333333333"
PLAN_ID_2 = "44444444-4444-4444-4444-444444444444"
REPORT_ID = "55555555-5555-5555-5555-555555555555"
FLEET_ID = "66666666-6666-6666-6666-666666666666"
FLEET_ID_2 = "77777777-7777-7777-7777-777777777777"


def _feature(layer, geometry, **props):
    return {
        "type": "Feature",
        "properties": {"layer": layer, "_valid": True, **props},
        "geometry": geometry,
    }


def _environment(env_id=ENV_ID, layers=None):
    return EnvironmentDetail(
        id=env_id,
        name="Сцена",
        uploaded_at=NOW,
        status="Корректна",
        counts=LayerCounts(airspace=1),
        errors=[],
        layers=layers
        or {
            "launch_site": [],
            "airspace": [
                _feature(
                    "airspace",
                    mapping(Polygon([(37.5, 55.7), (37.6, 55.7), (37.6, 55.8), (37.5, 55.8)])),
                    h_min=0,
                    h_max=300,
                )
            ],
            "no_fly": [],
            "obstacle": [],
            "reserve_site": [],
        },
    )


def _task(task_id=TASK_ID, version=1, status="Черновик"):
    return TaskDetail(
        id=task_id,
        name="Задача",
        environment_id=ENV_ID,
        environment_name="Сцена",
        survey_type="RGB",
        work_date=date(2026, 6, 15),
        status=status,
        version=version,
        daylight_warning=None,
        created_at=NOW,
        updated_at=NOW,
        gsd_cm=5.0,
        window_start=time(6, 0),
        window_end=time(20, 0),
        wind_speed_ms=3.0,
        cloud_cover_pct=10.0,
        criterion_mode="Время",
        criterion_alpha=1.0,
        area=mapping(Polygon([(37.52, 55.72), (37.55, 55.72), (37.55, 55.75), (37.52, 55.75)])),
    )


def _plan(plan_id=PLAN_ID, version=1):
    route = LineString([(37.52, 55.72), (37.55, 55.72), (37.55, 55.75)])
    tracks = MultiLineString([[(37.52, 55.72), (37.55, 55.72)]])
    return PlanDetail(
        id=plan_id,
        task_id=TASK_ID,
        version=version,
        created_at=NOW,
        criterion_mode="Время",
        criterion_alpha=1.0,
        j1_s=1200.0,
        j2_s=2400.0,
        is_optimal=False,
        uav_model="Геоскан Gemini",
        sortie_count=1,
        warnings=["предупреждение"],
        model_key="geoscan-gemini",
        camera_key="pollux",
        height_m=250.0,
        swath_m=300.0,
        cruise_speed_mps=15.0,
        budget_s=3000.0,
        sorties=[
            PlanSortie(
                uav_id="GEM-01",
                sortie_index=0,
                takeoff_site="ВПП Северная",
                landing_site="ВПП Северная",
                start_utc=NOW,
                end_utc=NOW,
                flight_time_s=900.0,
                distance_m=12000.0,
                track_geojson=mapping(route),
                survey_tracks_geojson=mapping(tracks),
            )
        ],
    )


# --- геометрия ----------------------------------------------------------------


def test_geometry_round_trip_preserves_coordinates(db):
    """Круг shapely -> PostGIS -> shapely не должен менять координаты.

    Именно поэтому геометрия читается через shapely, а не через ST_AsGeoJSON,
    который обрезал бы значения до девяти знаков.
    """
    cases = [
        Polygon(
            [(37.5000000012345, 55.7), (37.6, 55.7), (37.6, 55.8), (37.5, 55.8)],
            [[(37.52, 55.72), (37.54, 55.72), (37.54, 55.74), (37.52, 55.74)]],
        ),
        LineString([(37.5, 55.7), (37.6000000098765, 55.8)]),
    ]
    for geom in cases:
        restored = shape(from_db_geojson(to_db(geom)))
        assert restored.equals_exact(geom, 0.0), geom.geom_type


def test_single_part_multilinestring_fits_typmod(db):
    """Колонка объявлена MULTILINESTRING — одиночный LineString надо приводить."""
    line = LineString([(37.5, 55.7), (37.6, 55.8)])
    stored = to_db_multiline(line)
    restored = shape(from_db_geojson(stored))
    assert restored.geom_type == "MultiLineString"
    assert list(restored.geoms[0].coords) == list(line.coords)


# --- обстановка ---------------------------------------------------------------


def test_environment_features_returned_byte_for_byte(db):
    """Сырой GeoJSON возвращается тем же, включая служебные свойства.

    Фронтенд и проверки опираются на ``properties._valid`` и
    ``_buffer_geojson``, а также на «лишние» свойства из файла оператора —
    нормализованная через PostGIS копия их бы потеряла.
    """
    buffer_geojson = mapping(Polygon([(37.51, 55.71), (37.53, 55.71), (37.53, 55.73)]))
    original = _environment(
        layers={
            "launch_site": [_feature("launch_site", {"type": "Point", "coordinates": [37.55, 55.75]}, name="ВПП")],
            "airspace": [],
            "no_fly": [
                _feature(
                    "no_fly",
                    mapping(Polygon([(37.52, 55.72), (37.54, 55.72), (37.54, 55.74)])),
                    safety_buffer_m=150,
                    _buffer_geojson=buffer_geojson,
                    произвольное_свойство="сохранить",
                )
            ],
            "obstacle": [],
            "reserve_site": [],
        }
    )
    repositories.environments.put(original.id, original)
    reload(db)

    restored = repositories.environments.get(original.id)
    no_fly = restored.layers["no_fly"][0]
    # Свойства — как есть, включая служебные и «лишние» из файла оператора.
    assert no_fly["properties"]["произвольное_свойство"] == "сохранить"
    assert no_fly["properties"]["_valid"] is True
    assert no_fly["properties"]["safety_buffer_m"] == 150
    assert shape(no_fly["properties"]["_buffer_geojson"]).equals_exact(shape(buffer_geojson), 0.0)
    # Геометрия — та же (JSONB нормализует кортежи координат в списки, что на
    # сериализацию в ответе не влияет).
    for layer, features in original.layers.items():
        assert len(restored.layers[layer]) == len(features)
        for restored_feature, original_feature in zip(restored.layers[layer], features):
            assert shape(restored_feature["geometry"]).equals_exact(
                shape(original_feature["geometry"]), 0.0
            )


def test_environment_issue_order_is_preserved(db):
    env = _environment()
    env.status = "Содержит ошибки"
    env.errors = [
        ValidationIssue(layer="no_fly", feature_index=0, message="первая", name=None),
        ValidationIssue(layer="obstacle", feature_index=1, message="вторая", name="Вышка"),
    ]
    repositories.environments.put(env.id, env)
    reload(db)

    restored = repositories.environments.get(env.id)
    assert [e.message for e in restored.errors] == ["первая", "вторая"]
    assert [e.name for e in restored.errors] == [None, "Вышка"]


def test_environment_list_is_newest_first(db):
    for index, env_id in enumerate(
        ["aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"]
    ):
        env = _environment(env_id)
        env.uploaded_at = datetime(2026, 6, 15, 8 + index, tzinfo=timezone.utc)
        repositories.environments.put(env.id, env)
    reload(db)

    listed = repositories.environments.list_newest_first()
    assert [e.id for e in listed] == [
        "aaaaaaaa-0000-0000-0000-000000000002",
        "aaaaaaaa-0000-0000-0000-000000000001",
    ]


# --- задача -------------------------------------------------------------------


def test_task_update_replaces_row_and_keeps_environment_name(db):
    repositories.environments.put(ENV_ID, _environment())
    repositories.tasks.put(TASK_ID, _task())

    updated = _task(version=2, status="Рассчитана")
    updated.name = "Задача, переименованная"
    repositories.tasks.put(TASK_ID, updated)
    reload(db)

    restored = repositories.tasks.get(TASK_ID)
    assert (restored.name, restored.version, restored.status) == (
        "Задача, переименованная", 2, "Рассчитана",
    )
    # environment_name не хранится копией — берется из обстановки.
    assert restored.environment_name == "Сцена"
    assert shape(restored.area).equals_exact(shape(_task().area), 0.0)


def test_missing_records_raise_key_error(db):
    """Роутеры переводят KeyError в 404 — контракт не должен зависеть от хранилища."""
    for repo, method in (
        (repositories.environments, "get"),
        (repositories.tasks, "get"),
        (repositories.plans, "get"),
    ):
        try:
            getattr(repo, method)("99999999-9999-9999-9999-999999999999")
        except KeyError:
            continue
        raise AssertionError(f"{repo} не сообщил об отсутствии записи")


def test_malformed_id_is_not_found_rather_than_error(db):
    """Мусор в пути — это 404, а не 500."""
    try:
        repositories.tasks.get("не-uuid")
    except KeyError:
        return
    raise AssertionError("ожидался KeyError")


# --- план ---------------------------------------------------------------------


def test_plan_versions_increment_per_task(db):
    repositories.environments.put(ENV_ID, _environment())
    repositories.tasks.put(TASK_ID, _task())

    assert repositories.plans.next_version(TASK_ID) == 1
    repositories.plans.add(_plan(PLAN_ID, version=1))
    assert repositories.plans.next_version(TASK_ID) == 2
    repositories.plans.add(_plan(PLAN_ID_2, version=2))
    reload(db)

    listed = repositories.plans.list_by_task_newest_first(TASK_ID)
    assert [p.version for p in listed] == [2, 1]


def test_plan_route_geometry_survives_storage(db):
    repositories.environments.put(ENV_ID, _environment())
    repositories.tasks.put(TASK_ID, _task())
    original = _plan()
    repositories.plans.add(original)
    reload(db)

    restored = repositories.plans.get(PLAN_ID)
    sortie, original_sortie = restored.sorties[0], original.sorties[0]
    assert shape(sortie.track_geojson).equals_exact(shape(original_sortie.track_geojson), 0.0)
    assert shape(sortie.survey_tracks_geojson).equals_exact(
        shape(original_sortie.survey_tracks_geojson), 0.0
    )
    assert restored.warnings == ["предупреждение"]


# --- проверка безопасности -----------------------------------------------------


def _report(plan_id=PLAN_ID, status="Есть нарушения", attempts=0):
    return SafetyReport(
        id=REPORT_ID,
        plan_id=plan_id,
        task_id=TASK_ID,
        created_at=NOW,
        status=status,
        auto_recalc_count=attempts,
        checks=[
            SafetyCheckOut(name="geozones", label="Геозоны", passed=False, violations=["пересечение"]),
            SafetyCheckOut(name="energy", label="Энергия", passed=True, violations=[]),
        ],
    )


def test_report_after_recalc_is_found_by_both_plan_ids(db):
    """Автопересчет (БЕЗ.ФТ.3) меняет версию плана — отчет нужен по обеим."""
    repositories.environments.put(ENV_ID, _environment())
    repositories.tasks.put(TASK_ID, _task())
    repositories.plans.add(_plan(PLAN_ID, version=1))
    repositories.plans.add(_plan(PLAN_ID_2, version=2))

    repositories.safety.add_report([PLAN_ID, PLAN_ID_2], _report(plan_id=PLAN_ID_2, attempts=3))
    reload(db)

    by_requested = repositories.safety.latest_report(PLAN_ID)
    by_actual = repositories.safety.latest_report(PLAN_ID_2)
    assert by_requested is not None and by_actual is not None
    assert by_requested.id == by_actual.id  # один отчет, а не два
    assert by_actual.plan_id == PLAN_ID_2
    assert by_actual.auto_recalc_count == 3
    assert [c.name for c in by_actual.checks] == ["geozones", "energy"]


def test_attempt_counter_is_keyed_by_task_version(db):
    """Правка задачи поднимает версию и тем самым сбрасывает счетчик (БЕЗ.ФТ.3)."""
    repositories.environments.put(ENV_ID, _environment())
    repositories.tasks.put(TASK_ID, _task())

    repositories.safety.set_attempts(TASK_ID, 1, 2)
    assert repositories.safety.get_attempts(TASK_ID, 1) == 2
    assert repositories.safety.get_attempts(TASK_ID, 2) == 0

    repositories.safety.set_attempts(TASK_ID, 1, 3)
    assert repositories.safety.get_attempts(TASK_ID, 1) == 3


# --- парк БВС ------------------------------------------------------------------


def _fleet(fleet_id=FLEET_ID, number="GEM-01", name="Парк Северный"):
    return FleetDetail(
        id=fleet_id,
        name=name,
        location_lat=55.75,
        location_lon=37.60,
        location_name="Москва",
        uploaded_at=NOW,
        status="Корректна",
        total=1,
        ready_count=1,
        errors=[],
        instances=[
            FleetInstance(
                inventory_number=number,
                model_key="geoscan-gemini",
                model_name="Геоскан Gemini",
                base_launch_site="ВПП Северная",
                location_lat=55.75,
                location_lon=37.60,
                status="Готов",
                valid=True,
                error=None,
            )
        ],
    )


def test_second_fleet_does_not_replace_the_first(db):
    """Парков несколько: вторая загрузка не отменяет первую (расширение
    ПБС.ФТ.11 — задача ссылается на конкретный парк, «текущего» больше нет)."""
    repositories.fleet.add(_fleet(FLEET_ID, "GEM-01", name="Первый"))
    repositories.fleet.add(_fleet(FLEET_ID_2, "GEM-02", name="Второй"))
    reload(db)

    first = repositories.fleet.get(FLEET_ID)
    second = repositories.fleet.get(FLEET_ID_2)
    assert [i.inventory_number for i in first.instances] == ["GEM-01"]
    assert [i.inventory_number for i in second.instances] == ["GEM-02"]
    assert {f.id for f in repositories.fleet.list_all()} == {FLEET_ID, FLEET_ID_2}


def test_fleet_location_survives_the_round_trip(db):
    repositories.fleet.add(_fleet())
    reload(db)

    fleet = repositories.fleet.get(FLEET_ID)
    assert (fleet.location_lat, fleet.location_lon) == (55.75, 37.60)
    assert fleet.location_name == "Москва"
    assert (fleet.instances[0].location_lat, fleet.instances[0].location_lon) == (55.75, 37.60)


def test_unknown_fleet_is_none(db):
    assert repositories.fleet.get(FLEET_ID) is None
    assert repositories.fleet.list_all() == []
