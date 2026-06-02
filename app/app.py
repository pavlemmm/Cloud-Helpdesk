import os
import time

import psycopg2
import redis
from flask import Flask, redirect, render_template, request
from prometheus_client import Counter, Gauge
from prometheus_flask_exporter import PrometheusMetrics


app = Flask(__name__)
metrics = PrometheusMetrics(app)
total_tickets_gauge = Gauge("helpdesk_total_tickets", "Current number of helpdesk tickets")
page_visits_gauge = Gauge("helpdesk_page_visits", "Current number of page visits")
database_up_gauge = Gauge("helpdesk_database_up", "Database health status")
redis_up_gauge = Gauge("helpdesk_redis_up", "Redis health status")
tickets_deleted_counter = Counter(
    "helpdesk_tickets_deleted_total",
    "Total number of deleted helpdesk tickets",
)

DB_NAME = os.getenv("POSTGRES_DB", "companydb")
DB_USER = os.getenv("POSTGRES_USER", "admin")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "admin123")
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def get_db_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )


def init_db():
    initial_tasks = [
        "Ne radi štampač u kancelariji",
        "Problem sa pristupom VPN-u",
        "Resetovanje lozinke za email nalog",
    ]

    for attempt in range(1, 21):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            title TEXT NOT NULL
                        )
                        """
                    )
                    cur.execute("SELECT COUNT(*) FROM tasks")
                    task_count = cur.fetchone()[0]

                    if task_count == 0:
                        for task in initial_tasks:
                            cur.execute(
                                "INSERT INTO tasks (title) VALUES (%s)",
                                (task,),
                            )
                conn.commit()
            return
        except psycopg2.OperationalError:
            if attempt == 20:
                raise
            time.sleep(2)


def fetch_tasks():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title FROM tasks ORDER BY id ASC")
            return cur.fetchall()


def update_custom_metrics(ticket_count, visit_count):
    total_tickets_gauge.set(ticket_count)
    page_visits_gauge.set(visit_count)


def update_health_metrics(database_ok, redis_ok):
    database_up_gauge.set(1 if database_ok else 0)
    redis_up_gauge.set(1 if redis_ok else 0)


def check_dependencies():
    db_ok = True
    redis_ok = True

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception:
        db_ok = False

    try:
        redis_client.ping()
    except Exception:
        redis_ok = False

    update_health_metrics(db_ok, redis_ok)
    return db_ok, redis_ok


@app.route("/", methods=["GET"])
def index():
    redis_client.incr("page_visits")
    visit_count = int(redis_client.get("page_visits") or 0)
    tickets = fetch_tasks()
    update_custom_metrics(len(tickets), visit_count)
    check_dependencies()
    return render_template(
        "index.html",
        tickets=tickets,
        ticket_count=len(tickets),
        visit_count=visit_count,
    )


@app.route("/add-task", methods=["POST"])
def add_task():
    title = request.form.get("title", "").strip()
    if title:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO tasks (title) VALUES (%s)", (title,))
            conn.commit()
    return redirect("/")


@app.route("/delete-task/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        conn.commit()
    tickets_deleted_counter.inc()
    return redirect("/")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
