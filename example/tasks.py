#!/usr/bin/env python3
"""
Task Runner for Web Automation
Query and execute tasks by ID from agisdk
"""

import agisdk.tasks as tasks
import sys
import json
from typing import Dict, Any, Optional


def list_all_tasks():
    """List all available tasks"""
    print("Available Tasks:")
    print("=" * 50)
    for task in tasks.all:
        print(f"ID: {task['id']}")
        print(f"Goal: {task['goal']}")
        print(f"Website: {task['website']['name']} ({task['website']['url']})")
        print("-" * 30)


def get_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific task by ID"""
    for task in tasks.all:
        if task['id'] == task_id:
            return task
    return None


def show_task_details(task_id: str):
    """Show detailed information about a specific task"""
    task = get_task_by_id(task_id)
    if not task:
        print(f"Task '{task_id}' not found!")
        return
    
    print(f"Task ID: {task['id']}")
    print(f"Goal: {task['goal']}")
    print(f"Website: {task['website']['name']}")
    print(f"URL: {task['website']['url']}")
    if 'similarTo' in task['website']:
        print(f"Similar to: {task['website']['similarTo']}")
    if 'previewImage' in task['website']:
        print(f"Preview: {task['website']['previewImage']}")


def execute_task(task_id: str):
    """Execute a specific task"""
    task = get_task_by_id(task_id)
    if not task:
        print(f"Task '{task_id}' not found!")
        return
    
    print(f"Executing task: {task_id}")
    print(f"Goal: {task['goal']}")
    print(f"Website: {task['website']['name']} - {task['website']['url']}")
    
    # Here you would implement the actual task execution
    # For now, just show what would be executed
    print("\n[Task execution would happen here]")
    print("This is where the web automation would run to:")
    print(f"- Navigate to {task['website']['url']}")
    print(f"- Complete goal: {task['goal']}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tasks.py list                    # List all tasks")
        print("  python tasks.py show <task_id>          # Show task details")
        print("  python tasks.py exec <task_id>          # Execute a task")
        print("  python tasks.py search <keyword>        # Search tasks by keyword")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "list":
        list_all_tasks()
    
    elif command == "show":
        if len(sys.argv) < 3:
            print("Please provide a task ID")
            sys.exit(1)
        show_task_details(sys.argv[2])
    
    elif command == "exec":
        if len(sys.argv) < 3:
            print("Please provide a task ID")
            sys.exit(1)
        execute_task(sys.argv[2])
    
    elif command == "search":
        if len(sys.argv) < 3:
            print("Please provide a search keyword")
            sys.exit(1)
        keyword = sys.argv[2].lower()
        print(f"Searching for tasks containing '{keyword}':")
        print("=" * 50)
        found = False
        for task in tasks.all:
            if (keyword in task['id'].lower() or 
                keyword in task['goal'].lower() or 
                keyword in task['website']['name'].lower()):
                print(f"ID: {task['id']}")
                print(f"Goal: {task['goal']}")
                print(f"Website: {task['website']['name']}")
                print("-" * 30)
                found = True
        if not found:
            print("No tasks found matching the keyword.")
    
    else:
        print(f"Unknown command: {command}")
        print("Use 'list', 'show', 'exec', or 'search'")


if __name__ == "__main__":
    main()