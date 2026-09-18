"""Приложение Celery: брокер, лимиты времени и режим одной длинной задачи.

Почему Celery, а не RQ (решение плана обертки, не менять без причины):

* ``soft_time_limit`` бросает исключение **внутри** задачи — на 1800 с
  (ПЛН.ФТ.3) воркер успевает записать в ``plan_jobs`` статус «Остановлен по
  лимиту времени», а не оставить строку в «Выполняется» до сторожа;
* ``revoke(terminate=True)`` дает честную эскалацию после кооперативного
  флага отмены и снимает еще не начатую работу прямо из очереди;
* ``acks_late`` + ``worker_prefetch_multiplier=1`` — штатная конфигурация под
  «одна длинная задача на воркер».

``task_ignore_result=True``: результат работы — строка ``plan_jobs``,
дублировать его в Redis незачем.

``task_reject_on_worker_lost`` оставлен выключенным сознательно. Включенный,
он переотправил бы в очередь получасовой расчет, о котором оператор уже видит
статус; вместо этого работа остается «Выполняется» без heartbeat, и ленивый
сторож при следующем чтении переводит ее в «Ошибка» с текстом «расчет прерван
перезапуском сервиса» — ровно то поведение, которого требует план обертки.
"""

from __future__ import annotations

from celery import Celery

from uav_planner.config import Settings, get_settings


def build_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("gals", broker=settings.broker_url)
    app.conf.update(
        task_default_queue=settings.celery_queue,
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=settings.plan_time_limit_s,
        task_time_limit=settings.plan_hard_time_limit_s,
        task_always_eager=settings.celery_task_always_eager,
        # В eager-режиме падение задачи не должно прорастать в вызывающий код:
        # результат работы — строка plan_jobs со статусом «Ошибка», как в проде.
        task_eager_propagates=False,
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        worker_send_task_events=False,
    )
    # Без force=True: поиск задач отложен до финализации приложения. Иначе
    # импорт uav_planner.jobs.tasks пошел бы прямо отсюда, а он импортирует
    # celery_app — кольцо на первом же импорте.
    app.autodiscover_tasks(["uav_planner.jobs"], related_name="tasks")
    return app


celery_app = build_celery_app()


def is_eager() -> bool:
    """В eager-режиме управляющие команды (revoke) не к кому адресовать."""
    return bool(celery_app.conf.task_always_eager)
