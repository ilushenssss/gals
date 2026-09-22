# Пакетные имена указывают на новый решатель (cluster: кластеризация по площадкам
# + TSP-тур + балансировка узкого места).
#
# Переходное состояние на время слияния ядра с обёрткой сервиса: старый greedy
# (LPT) ещё жив и импортируется явно из модуля теми, кто на него завязан —
# services/plan_service.py и tests/test_routing_greedy.py. Переключение
# plan_service на cluster_assign_and_route — шаг 4 слияния, тем же коммитом
# greedy.py и его тесты удаляются, а этот комментарий снимается.
from .cluster import RoutingResult, Sortie, Track, Vehicle, cluster_assign_and_route

__all__ = ["Vehicle", "Track", "Sortie", "RoutingResult", "cluster_assign_and_route"]
