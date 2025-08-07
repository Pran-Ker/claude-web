from agisdk.REAL.tasks import all_tasks as tasks

def get_task_details(task_name):
    selected = [t for t in tasks if t["id"] == task_name]
    if selected:
        task = selected[0]
        return {
            'id': task['id'],
            'goal': task['goal'],
            'website': task['website']
        }
    return None

if __name__ == "__main__":
    task_name = input("Enter task name: ")
    details = get_task_details(task_name)
    print(details)