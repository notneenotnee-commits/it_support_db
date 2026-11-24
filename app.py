import json
import os
import random
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tickets.db")

app = Flask(__name__)


def get_db():
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  with get_db() as conn:
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
  return {
      "id": row["id"],
      "title": row["title"],
      "description": row["description"],
      "date": row["date"],
      "priority": row["priority"],
      "status": row["status"],
      "assignee": row["assignee"],
      "history": json.loads(row["history_json"]),
  }


# Инициализируем таблицу сразу при импорте модуля (важно при запуске через gunicorn)
init_db()


@app.route("/")
def index():
  return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/tickets", methods=["GET"])
def get_tickets():
  with get_db() as conn:
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

  with get_db() as conn:
    existing_ids = {
        r["id"] for r in conn.execute("SELECT id FROM tickets").fetchall()
    }
    ticket_id = random.randint(100, 999)
    while ticket_id in existing_ids:
      ticket_id = random.randint(100, 999)

    history = [{"date": now, "text": "Заявка создана.", "internal": False}]
    conn.execute(
        """
            INSERT INTO tickets (id, title, description, date, priority, status, assignee, history_json)
            VALUES (?, ?, ?, ?, 'Средний', 'новое', 'Не назначен', ?)
        """,
        (
            ticket_id,
            title,
            description,
            now,
            json.dumps(history, ensure_ascii=False),
        ),
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

  with get_db() as conn:
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
      return jsonify({"error": "Заявка не найдена"}), 404

    ticket = row_to_dict(row)
    now = datetime.now().strftime("%d.%m.%Y, %H:%M")

    new_prio = data.get("priority", ticket["priority"])
    new_stat = data.get("status", ticket["status"])
    comment = (data.get("comment") or "").strip()
    is_internal = bool(data.get("internal", False))

    if ticket["priority"] != new_prio:
      ticket["history"].append({
          "date": now,
          "text": f'Приоритет изменен с "{ticket["priority"]}" на "{new_prio}".',
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
          f"{author_prefix}{comment}" if is_internal else f"Комментарий: {comment}"
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
  with get_db() as conn:
    cur = conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    conn.commit()
    if cur.rowcount == 0:
      return jsonify({"error": "Заявка не найдена"}), 404
  return jsonify({"status": "deleted", "id": ticket_id})


if __name__ == "__main__":
  init_db()
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port, debug=False)