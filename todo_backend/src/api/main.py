from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Generator, List, Optional

from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# App + OpenAPI metadata
# ---------------------------------------------------------------------------

openapi_tags = [
    {"name": "Health", "description": "Service health and diagnostics."},
    {"name": "Tasks", "description": "CRUD operations for todo tasks."},
]

app = FastAPI(
    title="Todo Backend API",
    description=(
        "Minimalistic todo application backend. Provides CRUD operations for tasks "
        "and marking tasks as completed, backed by SQLite."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

# NOTE: Frontend runs on port 3000. Allow that origin explicitly.
# You can extend this list if you deploy elsewhere.
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

# Database container advertises SQLITE_DB env var. If not set, use local file.
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "todo.db")
DB_PATH = os.environ.get("SQLITE_DB", DEFAULT_DB_PATH)


def _ensure_parent_dir(db_path: str) -> None:
    """Ensure the directory containing the SQLite file exists."""
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    """Create a new sqlite3 connection with sensible defaults."""
    _ensure_parent_dir(DB_PATH)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    """Create the tasks table if it doesn't exist."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )


@app.on_event("startup")
def _on_startup() -> None:
    """Initialize SQLite schema on startup."""
    _init_db()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """Represents a todo task."""

    id: int = Field(..., description="Task ID.")
    title: str = Field(..., description="Task title.")
    completed: bool = Field(..., description="Whether the task is completed.")
    created_at: str = Field(..., description="ISO8601 creation timestamp.")


class TaskCreate(BaseModel):
    """Request model to create a task."""

    title: str = Field(..., min_length=1, max_length=200, description="Task title.")


class TaskUpdate(BaseModel):
    """Request model to update a task (partial update)."""

    title: Optional[str] = Field(
        default=None, min_length=1, max_length=200, description="Updated task title."
    )
    completed: Optional[bool] = Field(
        default=None, description="Updated completed status."
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    """Convert a SQLite row to Task."""
    return Task(
        id=int(row["id"]),
        title=str(row["title"]),
        completed=bool(row["completed"]),
        created_at=str(row["created_at"]),
    )


def _fetch_task_or_404(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    """Fetch a task row or raise 404."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", tags=["Health"], summary="Health check", operation_id="health_check")
def health_check() -> Dict[str, str]:
    """Return service health status."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/tasks",
    tags=["Tasks"],
    summary="List tasks",
    operation_id="list_tasks",
    response_model=List[Task],
)
def list_tasks() -> List[Task]:
    """List all tasks, newest first."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC",
        ).fetchall()
    return [_row_to_task(r) for r in rows]


# PUBLIC_INTERFACE
@app.post(
    "/tasks",
    tags=["Tasks"],
    summary="Create task",
    operation_id="create_task",
    response_model=Task,
    status_code=201,
)
def create_task(payload: TaskCreate) -> Task:
    """Create a new task."""
    now = datetime.utcnow().isoformat()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, completed, created_at) VALUES (?, ?, ?)",
            (payload.title.strip(), 0, now),
        )
        task_id = int(cur.lastrowid)
        row = _fetch_task_or_404(conn, task_id)
    return _row_to_task(row)


# PUBLIC_INTERFACE
@app.get(
    "/tasks/{task_id}",
    tags=["Tasks"],
    summary="Get task by id",
    operation_id="get_task",
    response_model=Task,
)
def get_task(
    task_id: int = Path(..., ge=1, description="Task ID to fetch."),
) -> Task:
    """Fetch a single task by ID."""
    with _db() as conn:
        row = _fetch_task_or_404(conn, task_id)
    return _row_to_task(row)


# PUBLIC_INTERFACE
@app.put(
    "/tasks/{task_id}",
    tags=["Tasks"],
    summary="Update task",
    operation_id="update_task",
    response_model=Task,
)
def update_task(
    payload: TaskUpdate,
    task_id: int = Path(..., ge=1, description="Task ID to update."),
) -> Task:
    """Update a task's title and/or completed status."""
    if payload.title is None and payload.completed is None:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    with _db() as conn:
        existing = _fetch_task_or_404(conn, task_id)

        new_title = (
            payload.title.strip()
            if payload.title is not None
            else str(existing["title"])
        )
        new_completed = (
            int(bool(payload.completed))
            if payload.completed is not None
            else int(existing["completed"])
        )

        conn.execute(
            "UPDATE tasks SET title = ?, completed = ? WHERE id = ?",
            (new_title, new_completed, task_id),
        )
        row = _fetch_task_or_404(conn, task_id)
    return _row_to_task(row)


# PUBLIC_INTERFACE
@app.delete(
    "/tasks/{task_id}",
    tags=["Tasks"],
    summary="Delete task",
    operation_id="delete_task",
    status_code=204,
)
def delete_task(
    task_id: int = Path(..., ge=1, description="Task ID to delete."),
) -> None:
    """Delete a task."""
    with _db() as conn:
        _fetch_task_or_404(conn, task_id)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return None


# PUBLIC_INTERFACE
@app.post(
    "/tasks/{task_id}/toggle",
    tags=["Tasks"],
    summary="Toggle task completion",
    operation_id="toggle_task_completion",
    response_model=Task,
)
def toggle_task_completion(
    task_id: int = Path(..., ge=1, description="Task ID to toggle completion."),
) -> Task:
    """Toggle completed state for a task."""
    with _db() as conn:
        existing = _fetch_task_or_404(conn, task_id)
        new_completed = 0 if int(existing["completed"]) == 1 else 1
        conn.execute(
            "UPDATE tasks SET completed = ? WHERE id = ?",
            (new_completed, task_id),
        )
        row = _fetch_task_or_404(conn, task_id)
    return _row_to_task(row)
