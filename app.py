import json
import os
import random
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
import psycopg2
from psycopg2.extras import RealDictCursor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tickets.db")
DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)


def is_postgres():
  return bool(DATABASE_URL)


def get_pg_conn():
  url = DATABASE_URL
  if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
  return psycopg2.connect(url, cursor_factory=RealDictCursor)


def get_sqlite_conn():
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  if is_postgres():
    with get_pg_conn() as conn:
      with conn.cursor() as cur:
        cur.execute("""
                    CREATE TABLE IF NOT EXISTS tickets (
                        id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        date TEXT NOT NULL,
                        priority TEXT NOT NULL DEFAULT 'Средний',
                        status TEXT NOT NULL DEFAULT 'новое',
                        assignee TEXT NOT NULL DEFAULT 'Не назначен',
                        history_json TEXT NOT NULL
                    )
                """)
      conn.commit()
  else:
    with get_sqlite_conn() as conn:
      conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    date TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'Средний',
                    status TEXT NOT NULL DEFAULT 'новое',
                    assignee TEXT NOT NULL DEFAULT 'Не назначен',
                    history_json TEXT NOT NULL
                )
            """)
      conn.commit()


def row_to_dict(row):
  history_raw = row["history_json"]
  history = (
      history_raw if isinstance(history_raw, list) else json.loads(history_raw)
  )
  return {
      "id": row["id"],
      "title": row["title"],
      "description": row["description"],
      "date": row["date"],
      "priority": row["priority"],
      "status": row["status"],
      "assignee": row["assignee"],
      "history": history,
  }


init_db()


@app.route("/")
def index():
  return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/tickets", methods=["GET"])
def get_tickets():
  if is_postgres():
    with get_pg_conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM tickets ORDER BY id DESC")
        rows = cur.fetchall()
  else:
    with get_sqlite_conn() as conn:
      rows = conn.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall()
  return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/tickets", methods=["POST"])
def create_ticket():
  data = request.get_json(silent=True) or {}
  title = (data.get("title") or "").strip()
  description = (data.get("description") or "").strip()

  if not title or not description:
    return jsonify({"error": "Заполните тему и описание заявки"}), 400

  now = datetime.now().strftime("%d.%m.%Y, %H:%M")
  history = [{"date": now, "text": "Заявка создана.", "internal": False}]
  history_str = json.dumps(history, ensure_ascii=False)

  if is_postgres():
    with get_pg_conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT id FROM tickets")
        existing_ids = {r["id"] for r in cur.fetchall()}
        ticket_id = random.randint(100, 999)
        while ticket_id in existing_ids:
          ticket_id = random.randint(100, 999)

        cur.execute(
            """
                    INSERT INTO tickets (id, title, description, date, priority, status, assignee, history_json)
                    VALUES (%s, %s, %s, %s, 'Средний', 'новое', 'Не назначен', %s)
                """,
            (ticket_id, title, description, now, history_str),
        )
        cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        row = cur.fetchone()
      conn.commit()
  else:
    with get_sqlite_conn() as conn:
      existing_ids = {
          r["id"] for r in conn.execute("SELECT id FROM tickets").fetchall()
      }
      ticket_id = random.randint(100, 999)
      while ticket_id in existing_ids:
        ticket_id = random.randint(100, 999)

      conn.execute(
          """
                INSERT INTO tickets (id, title, description, date, priority, status, assignee, history_json)
                VALUES (?, ?, ?, ?, 'Средний', 'новое', 'Не назначен', ?)
            """,
          (ticket_id, title, description, now, history_str),
      )
      conn.commit()
      row = conn.execute(
          "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
      ).fetchone()

  return jsonify(row_to_dict(row)), 201


@app.route("/api/tickets/<int:ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
  data = request.get_json(silent=True) or {}
  role = data.get("role", "support")
  now = datetime.now().strftime("%d.%m.%Y, %H:%M")

  if is_postgres():
    with get_pg_conn() as conn:
      with conn.cursor() as cur:
        cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        row = cur.fetchone()
        if not row:
          return jsonify({"error": "Заявка не найдена"}), 404

        ticket = row_to_dict(row)
        new_prio = data.get("priority", ticket["priority"])
        new_stat = data.get("status", ticket["status"])
        comment = (data.get("comment") or "").strip()
        is_internal = bool(data.get("internal", False))

        if ticket["priority"] != new_prio:
          ticket["history"].append({
              "date": now,
              "text": (
                  f'Приоритет изменен с "{ticket["priority"]}" на "{new_prio}".'
              ),
              "internal": False,
          })
          ticket["priority"] = new_prio

        if ticket["status"] != new_stat:
          ticket["history"].append({
              "date": now,
              "text": f'Статус изменен с "{ticket["status"]}" на "{new_stat}".',
              "internal": False,
          })
          ticket["status"] = new_stat

        if role == "admin" and "assignee" in data:
          new_ass = (data.get("assignee") or "").strip() or "Не назначен"
          if ticket["assignee"] != new_ass:
            ticket["history"].append({
                "date": now,
                "text": f"Назначен исполнитель: {new_ass}.",
                "internal": False,
            })
            ticket["assignee"] = new_ass

        if comment:
          author_prefix = (
              "Администратор: " if role == "admin" else "Специалист: "
          )
          comment_text = (
              f"{author_prefix}{comment}"
              if is_internal
              else f"Комментарий: {comment}"
          )
          ticket["history"].append(
              {"date": now, "text": comment_text, "internal": is_internal}
          )

        cur.execute(
            """
                    UPDATE tickets
                    SET priority = %s, status = %s, assignee = %s, history_json = %s
                    WHERE id = %s
                """,
            (
                ticket["priority"],
                ticket["status"],
                ticket["assignee"],
                json.dumps(ticket["history"], ensure_ascii=False),
                ticket_id,
            ),
        )
        cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
        updated = cur.fetchone()
      conn.commit()
  else:
    with get_sqlite_conn() as conn:
      row = conn.execute(
          "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
      ).fetchone()
      if not row:
        return jsonify({"error": "Заявка не найдена"}), 404

      ticket = row_to_dict(row)
      new_prio = data.get("priority", ticket["priority"])
      new_stat = data.get("status", ticket["status"])
      comment = (data.get("comment") or "").strip()
      is_internal = bool(data.get("internal", False))

      if ticket["priority"] != new_prio:
        ticket["history"].append({
            "date": now,
            "text": (
                f'Приоритет изменен с "{ticket["priority"]}" на "{new_prio}".'
            ),
            "internal": False,
        })
        ticket["priority"] = new_prio

      if ticket["status"] != new_stat:
        ticket["history"].append({
            "date": now,
            "text": f'Статус изменен с "{ticket["status"]}" на "{new_stat}".',
            "internal": False,
        })
        ticket["status"] = new_stat

      if role == "admin" and "assignee" in data:
        new_ass = (data.get("assignee") or "").strip() or "Не назначен"
        if ticket["assignee"] != new_ass:
          ticket["history"].append({
              "date": now,
              "text": f"Назначен исполнитель: {new_ass}.",
              "internal": False,
          })
          ticket["assignee"] = new_ass

      if comment:
        author_prefix = "Администратор: " if role == "admin" else "Специалист: "
        comment_text = (
            f"{author_prefix}{comment}"
            if is_internal
            else f"Комментарий: {comment}"
        )
        ticket["history"].append(
            {"date": now, "text": comment_text, "internal": is_internal}
        )

      conn.execute(
          """
                UPDATE tickets
                SET priority = ?, status = ?, assignee = ?, history_json = ?
                WHERE id = ?
            """,
          (
              ticket["priority"],
              ticket["status"],
              ticket["assignee"],
              json.dumps(ticket["history"], ensure_ascii=False),
              ticket_id,
          ),
      )
      conn.commit()
      updated = conn.execute(
          "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
      ).fetchone()

  return jsonify(row_to_dict(updated))


@app.route("/api/tickets/<int:ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id):
  if is_postgres():
    with get_pg_conn() as conn:
      with conn.cursor() as cur:
        cur.execute("DELETE FROM tickets WHERE id = %s", (ticket_id,))
        deleted_count = cur.rowcount
      conn.commit()
      if deleted_count == 0:
        return jsonify({"error": "Заявка не найдена"}), 404
  else:
    with get_sqlite_conn() as conn:
      cur = conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
      conn.commit()
      if cur.rowcount == 0:
        return jsonify({"error": "Заявка не найдена"}), 404
  return jsonify({"status": "deleted", "id": ticket_id})


if __name__ == "__main__":
  init_db()
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port, debug=False)