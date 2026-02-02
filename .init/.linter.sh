#!/bin/bash
cd /home/kavia/workspace/code-generation/simple-task-manager-313362-313372/todo_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

