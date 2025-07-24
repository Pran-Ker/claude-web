#!/usr/bin/env python3
"""
Task evaluation script - checks if web automation tasks completed successfully.

Usage: python3 example/evaluate_task.py <task_id> <answer> [port]

Examples:
- python3 example/evaluate_task.py udriver-7 "6"
- python3 example/evaluate_task.py dashdish-10 ""
- python3 example/evaluate_task.py task-123 "answer" 9223

Results saved to evaluations/{task_id}.json
"""

import json
import sys
import os
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


def save_evaluation_results(task_id, task_details, env_state_json, evaluation_result):
    """
    Save evaluation results and environment state to JSON file.
    
    Args:
        task_id: Task identifier
        task_details: Task configuration from all_tasks
        env_state_json: Environment state from /finish endpoint
        evaluation_result: Results from evaluate_task function
    """
    try:
        # Create evaluations directory if it doesn't exist
        eval_dir = "evaluations"
        os.makedirs(eval_dir, exist_ok=True)
        
        # Simple filename without timestamp
        filename = f"{task_id}.json"
        filepath = os.path.join(eval_dir, filename)
        
        # Extract criteria results from evaluation info if available
        criteria_results = []
        if evaluation_result.get("info") and isinstance(evaluation_result["info"], dict):
            info = evaluation_result["info"]
            
            # Handle both JMESPath and LLM evaluations
            if "criterion_details" in info:
                # Structured criterion details
                for i, criterion in enumerate(info["criterion_details"]):
                    criteria_results.append({
                        "criterion": i,
                        "description": criterion.get("description", ""),
                        "actual_value": criterion.get("actual_value", ""),
                        "expected_value": criterion.get("expected_value", ""),
                        "is_correct": criterion.get("is_correct", False)
                    })
            elif "results" in info and isinstance(info["results"], list):
                # Parse agisdk evaluation results format (handles both lists and tuples)
                criterion_names = ["correct pickup location", "correct destination", "correct car type"]
                for i, result in enumerate(info["results"]):
                    if isinstance(result, (list, tuple)) and len(result) >= 2:
                        is_correct = result[0]
                        details = result[1] if isinstance(result[1], dict) else {}
                        criteria_results.append({
                            "criterion": i,
                            "description": criterion_names[i] if i < len(criterion_names) else f"criterion_{i}",
                            "actual_value": details.get("actual_value", ""),
                            "expected_value": details.get("expected_value", ""),
                            "is_correct": is_correct
                        })
            else:
                # Extract from evaluation details if available
                for key, value in info.items():
                    if "actual_value" in str(key).lower() or "expected_value" in str(key).lower():
                        criteria_results.append({
                            "criterion": key,
                            "value": value,
                            "description": f"Evaluation criterion: {key}"
                        })
        
        # Build comprehensive results object
        results = {
            "task_id": task_id,
            "task_goal": task_details.get("goal", "") if task_details else "",
            "success": evaluation_result.get("success", False),
            "reward": evaluation_result.get("reward", 0),
            "evaluation_message": evaluation_result.get("message", ""),
            "model_response": evaluation_result.get("model_response", ""),
            "criteria_results": criteria_results,
            "environment_state": env_state_json,
            "evaluation_info": evaluation_result.get("info", {}),
            "execution_details": {
                "website_url": task_details.get("website", {}).get("url", "") if task_details else "",
                "evaluation_method": "agisdk WebCloneEvaluator",
                "port_used": evaluation_result.get("port", 9222)
            }
        }
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"📊 Results saved to: {filepath}")
        return filepath
        
    except Exception as e:
        print(f"⚠️  Warning: Could not save results - {str(e)}")
        return None


def evaluate_task(task_id, model_response="", port=9222):
    """
    Generic task evaluation following the standard agisdk workflow.
    Supports both JMESPath queries and LLM boolean evaluations.
    
    Args:
        task_id: The task identifier (e.g., 'udriver-7', 'reddit-3')
        model_response: The answer to evaluate (e.g., "6", "license plate ABC123")
        port: Browser port to connect to (default: 9222)
    
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
        bot = WebTool(port=port)
        bot.connect()
        
        # Navigate to /finish endpoint to get environment state
        finish_url = f"{base_url}/finish"
        bot.go(finish_url)
        
        # Wait for page to load completely
        import time
        time.sleep(3)
        
        # Extract environment state - try multiple methods
        env_state_text = bot.js("document.querySelector('pre')?.textContent")
        if not env_state_text:
            # Fallback: get all text content and extract JSON
            full_content = bot.js("document.body.textContent")
            if full_content and '{' in full_content:
                # Find the first JSON object
                start_idx = full_content.find('{')
                if start_idx != -1:
                    # Count braces to find the complete JSON
                    brace_count = 0
                    end_idx = start_idx
                    for i, char in enumerate(full_content[start_idx:], start_idx):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    env_state_text = full_content[start_idx:end_idx]
        
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
        
        # Build structured results
        evaluation_result = {
            "task_id": task_id,
            "model_response": model_response,
            "reward": reward,
            "done": done,  
            "message": message,
            "info": info,
            "success": reward > 0,
            "port": port
        }
        
        # Save results to file only if successful
        if evaluation_result["success"]:
            save_evaluation_results(task_id, task_details, env_state_json, evaluation_result)
        
        return evaluation_result
        
    except Exception as e:
        return {
            "task_id": task_id,
            "model_response": model_response,
            "error": str(e),
            "success": False
        }


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        task_id = sys.argv[1]
        answer = sys.argv[2]
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 9222
        
        result = evaluate_task(task_id, answer, port)
        
        if result["success"]:
            print(f"✅ SUCCESS - Reward: {result['reward']}")
        else:
            error_msg = result.get('message', result.get('error', 'Unknown'))
            print(f"❌ FAILURE - {error_msg}")
    else:
        print("Usage: python evaluate_task.py <task_id> <answer> [port]")
        print("Example: python evaluate_task.py udriver-7 '6' 9223")