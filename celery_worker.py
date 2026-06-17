from app.tasks.celery_app import celery_app  # noqa: F401

if __name__ == "__main__":
    celery_app.start()
