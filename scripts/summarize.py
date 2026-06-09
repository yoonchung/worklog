import argparse
import json
import logging
import os
import sys
import re
import httpx
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Try to import Anthropic client in a couple of common ways
try:
    import anthropic
except Exception:
    anthropic = None


def load_anthropic_client():
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in .env or environment")

    # Try common client constructors
    if anthropic:
        client = getattr(anthropic, "Anthropic", None)
        if client:
            local_api_key = os.environ.get("LOCAL_API_KEY")
            local_base_url = os.environ.get("LOCAL_BASE_URL")

            if local_api_key and local_base_url:
                try:
                    httpx.get(local_base_url, timeout=3.0)
                    logger.info("Using local API: %s", local_base_url)
                    return client(base_url=local_base_url, api_key=local_api_key)
                except httpx.ConnectError:
                    logger.warning("Local LLM at %s is not reachable, falling back to production API.", local_base_url)

            logger.info("Using production Anthropic API")
            return client(api_key=key)
    raise RuntimeError("Could not initialize Anthropic client from library. Ensure 'anthropic' package is installed and up-to-date.")


def _get_field(item_obj, field, default=None):
    if isinstance(item_obj, dict):
        return item_obj.get(field, default)
    return getattr(item_obj, field, default)


def build_prompt(item_obj, item_type="pr"):
    if item_type == "commit":
        sha = _get_field(item_obj, "sha", "")
        message = ""
        author_name = ""
        commit_data = _get_field(item_obj, "commit", {})
        if isinstance(commit_data, dict):
            message = commit_data.get("message", "")
            author = commit_data.get("author", {})
            if isinstance(author, dict):
                author_name = author.get("name", "")
        if not message:
            message = _get_field(item_obj, "message", "")

        instruction = (
            "You are helping a software engineer document their work for future resume use.\n"
            "Given the following GitHub commit data, write a concise, resume-ready summary. Focus on:\n"
            "- What the commit changed or fixed\n"
            "- The technical approach or libraries involved\n"
            "- Any impact or reason behind the change\n"
            "Return a single JSON object with these fields:\n"
            "- commit_sha: string\n"
            "- summary: 1-2 sentence plain-language summary of the work\n"
            "- resume_bullet: a one-line bullet describing the result\n"
            "- is_resume_worthy: true or false\n"
            "- technical_points: array of short technical bullet points\n"
            "- notes: optional brief notes or follow-ups for improvement with examples\n"
            "- confidence_score: float 0.0-1.0"
        )

        data_section = {
            "sha": sha,
            "message": message,
            "author": author_name,
        }
    else:
        title = _get_field(item_obj, "title", "")
        description = _get_field(item_obj, "body", _get_field(item_obj, "description", ""))
        commit_messages = _get_field(item_obj, "commit_messages") or _get_field(item_obj, "commits") or []
        if isinstance(commit_messages, list):
            commit_messages = "\n".join(str(m) for m in commit_messages)

        instruction = (
            "You are helping a software engineer document their work for future resume use.\n"
            "Given the following pull request data, write a concise, resume-ready summary. Focus on:\n"
            "- The technical problem solved or feature built\n"
            "- Business impact if apparent\n"
            "- Scale or complexity if mentioned\n"
            "Return a single JSON object with these fields:\n"
            "- pr_number: integer\n"
            "- summary: 1-2 sentence plain-language summary of the work\n"
            "- resume_bullet: a one-line bullet describing the result\n"
            "- is_resume_worthy: true or false\n"
            "- technical_points: array of short technical bullet points\n"
            "- notes: optional brief notes or follow-ups for improvement with examples\n"
            "- confidence_score: float 0.0-1.0"
        )

        data_section = {
            "title": title,
            "description": description,
            "commit_messages": commit_messages,
        }

    prompt = (
        f"{instruction}\nDATA:\n{json.dumps(data_section, indent=2, default=str)}\n"
        f"Please respond with a single JSON object only."
    )
    return prompt


def call_anthropic(client, prompt, model="claude-haiku-4-5", max_tokens=512):
    try:
        resp = client.messages.create(
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            model=model,
        )
        return resp
    except Exception as e:
        raise RuntimeError(f"Anthropic API call failed: {e}") from e


def determine_item_type(item_obj):
    """Determine whether item_obj is a PR or commit based on its attributes."""
    if isinstance(item_obj, dict):
        # Check for commit-specific fields first
        if "sha" in item_obj or "commit" in item_obj:
            return "commit"
        # Check for PR-specific fields
        if "number" in item_obj and "title" in item_obj:
            return "pr"
        # Default to PR if we can't determine
        return "pr"
    # For non-dict objects, check for attributes
    if hasattr(item_obj, "sha") or hasattr(item_obj, "commit"):
        return "commit"
    if hasattr(item_obj, "number") and hasattr(item_obj, "title"):
        return "pr"
    return "pr"


def parse_llm_response(resp) -> dict:
    """Parse LLM response into a summary dict, handling various response formats."""
    if hasattr(resp, "content") and isinstance(resp.content, list) and len(resp.content) > 0:
        text = resp.content[0].text
    elif isinstance(resp, str):
        text = resp
    else:
        logger.warning("Could not parse LLM response as JSON, using raw text as summary")
        return {"summary": str(resp)}

    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    logger.warning("Could not parse LLM response as JSON, using raw text as summary")
    return {"summary": text or ""}

def summarize_items(item_list, client, item_type="pr", model="claude-haiku-4-5", max_tokens=512):
    results = []
    total = len(item_list)
    for i, item in enumerate(item_list, 1):
        logger.info("[%d/%d] Summarizing %s...", i, total, item_type)
        prompt = build_prompt(item, item_type=item_type)
        try:
            resp = call_anthropic(client, prompt, model=model, max_tokens=max_tokens)
        except Exception as e:
            logger.error("[%d/%d] Failed to summarize item: %s", i, total, e)
            continue

        parsed = parse_llm_response(resp)

        if item_type == "pr":
            parsed["pr_number"] = _get_field(item, "pr_number") or _get_field(item, "number")
        else:
            parsed["commit_sha"] = _get_field(item, "sha")
        parsed["_model_used"] = getattr(resp, "model", None) or model
        results.append(parsed)
        logger.info("[%d/%d] Done", i, total)
    return results


def resolve_input_path(user_input=None):
    """Resolve input file path with priority: CLI arg > pull_requests.json > stdin"""
    if user_input and user_input != "-":
        return user_input
    worklog_dir = Path(__file__).resolve().parent.parent
    default_json = worklog_dir / "pull_requests.json"
    if default_json.exists():
        return str(default_json)
    return None


def load_input(path_or_dash):
    if not path_or_dash or path_or_dash == "-":
        data = sys.stdin.read()
        if not data.strip():
            raise RuntimeError("No input on stdin")
        obj = json.loads(data)
        return obj
    else:
        with open(path_or_dash, "r", encoding="utf-8") as f:
            return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Summarize PR or commit JSON using Anthropic")
    parser.add_argument("input", nargs="?", default=None, help="Path to JSON file. Defaults to pull_requests.json in worklog, or stdin if not found.")
    parser.add_argument("output", nargs="?", default=None, help="Output JSON filename. Defaults to summaries.json in worklog root.")
    parser.add_argument("--model", default="claude-haiku-4-5", help="Anthropic model to use")
    parser.add_argument("--max-tokens", type=int, default=512, help="Max tokens to request from the model")
    args = parser.parse_args()

    input_path = resolve_input_path(args.input)
    client = load_anthropic_client()

    raw = load_input(input_path)
    if isinstance(raw, dict):
        item_list = [raw]
    elif isinstance(raw, list):
        item_list = raw
    else:
        raise RuntimeError("Input JSON must be an object or array of objects representing PRs or commits")

    item_type = determine_item_type(item_list[0])
    output_filename = args.output if args.output else "summaries.json"
    output_path = Path(__file__).resolve().parent.parent / output_filename
    summaries = []  # Initialize to avoid unbound error in except block

    try:
        summaries = summarize_items(item_list, client, item_type=item_type, model=args.model, max_tokens=args.max_tokens)
    except Exception as e:
        logger.error("Error during summarization: %s", e)
        if summaries:
            logger.warning("Saving %d partial results to %s", len(summaries), output_path)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(summaries, f, indent=2, ensure_ascii=False)
        raise

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(summaries, f, indent=2, ensure_ascii=False)
    logger.info("Summaries saved to %s", output_path)


if __name__ == "__main__":
    main()
