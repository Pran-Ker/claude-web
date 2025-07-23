#!/usr/bin/env python3
"""
Generic task evaluation script following the agisdk evaluation pattern.
Supports both JMESPath queries and LLM boolean evaluations for any webclone task.

HOW THIS WORKS:
1. Connect to browser on port 9222
2. Navigate to {task_website}/finish to get environment state JSON
3. Use official agisdk WebCloneEvaluator to check task completion
4. Two evaluation types are handled automatically:
   - JMESPath: Extracts specific values from environment state (most tasks)
     Example: differences.currentTrips.added."6".pickup.name == "Airport"
   - LLM Boolean: Uses natural language to evaluate answers (retrieval tasks)
     Example: "Does the answer reflect that six rides were taken in June?"
5. Task succeeds only if ALL evaluation criteria pass

USAGE:
python3 example/evaluate_task.py <task_id> <answer>
- For JMESPath tasks: answer can be empty "" (checks environment state)
- For LLM boolean tasks: provide your extracted answer like "6" or "license ABC123"

EXAMPLES:
python3 example/evaluate_task.py udriver-7 "6"        # LLM boolean evaluation
python3 example/evaluate_task.py dashdish-10 ""       # JMESPath evaluation
"""

import json
import sys
from tools.web_tool import WebTool
from agisdk.REAL.browsergym.webclones.task_config import TaskConfig
from agisdk.REAL.browsergym.webclones.evaluate import WebCloneEvaluator
from agisdk.REAL.tasks import all_tasks


def get_task_details(task_id):
    """Get task details by ID from all_tasks."""
    for task in all_tasks:
        if task["id"] == task_id:
            return task
    return None


def evaluate_task(task_id, model_response=""):
    """
    Generic task evaluation following the standard agisdk workflow.
    Supports both JMESPath queries and LLM boolean evaluations.
    
    Args:
        task_id: The task identifier (e.g., 'udriver-7', 'reddit-3')
        model_response: The answer to evaluate (e.g., "6", "license plate ABC123")
    
    Returns:
        dict: Evaluation results with success, reward, message, etc.
    """
    try:
        # Get task details from agisdk.REAL.tasks
        task_details = get_task_details(task_id)
        if not task_details:
            return {
                "task_id": task_id,
                "error": f"Task {task_id} not found",
                "success": False
            }
        
        # Get base URL from task configuration
        base_url = task_details['website']['url']
        
        # Connect to browser
        bot = WebTool(port=9222)
        bot.connect()
        
        # Navigate to /finish endpoint to get environment state
        finish_url = f"{base_url}/finish"
        bot.go(finish_url)
        
        # Extract environment state from <pre> element
        env_state_text = bot.js("document.querySelector('pre')?.textContent")
        
        if not env_state_text:
            bot.close()
            return {
                "task_id": task_id,
                "error": "Could not retrieve environment state from /finish endpoint",
                "success": False
            }
        
        # Parse environment state JSON
        try:
            env_state_json = json.loads(env_state_text)
        except json.JSONDecodeError as e:
            bot.close()
            return {
                "task_id": task_id,
                "error": f"Invalid JSON format: {str(e)}",
                "success": False
            }
        
        bot.close()
        
        # Create TaskConfig and evaluator
        task_config = TaskConfig(task_id)
        evaluator = WebCloneEvaluator(task_config=task_config)
        
        # Run evaluation - this handles both JMESPath and LLM boolean
        reward, done, message, info = evaluator.evaluate(
            env_state=env_state_json, 
            model_response=model_response
        )
        
        print(f"Evaluation result: {message}, Reward: {reward}")
        
        # Return structured results
        return {
            "task_id": task_id,
            "model_response": model_response,
            "reward": reward,
            "done": done,  
            "message": message,
            "info": info,
            "success": reward > 0,
            "evaluation_time": None
        }
        
    except Exception as e:
        return {
            "task_id": task_id,
            "model_response": model_response,
            "error": str(e),
            "success": False
        }


if __name__ == "__main__":
    if len(sys.argv) == 3:
        task_id = sys.argv[1]
        answer = sys.argv[2]
        
        result = evaluate_task(task_id, answer)
        
        if result["success"]:
            print(f"✅ SUCCESS - Reward: {result['reward']}")
        else:
            error_msg = result.get('message', result.get('error', 'Unknown'))
            print(f"❌ FAILURE - {error_msg}")
    else:
        print("Usage: python evaluate_task.py <task_id> <answer>")
        print("Example: python evaluate_task.py udriver-7 '6'")