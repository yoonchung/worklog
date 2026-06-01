import argparse
import json
import os
import sys
from dotenv import load_dotenv

# Try to import Anthropic client in a couple of common ways
try:
    import anthropic
except Exception:
    anthropic = None

HUMAN_PROMPT = getattr(anthropic, "HUMAN_PROMPT", "Human:") if anthropic else "Human:"
AI_PROMPT = getattr(anthropic, "AI_PROMPT", "Assistant:") if anthropic else "Assistant:"


def load_anthropic_client():
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in .env or environment")

    # Try common client constructors
    if anthropic:
        Client = getattr(anthropic, "Client", None) or getattr(anthropic, "Anthropic", None)
        if Client:
            return Client(api_key=key)
    raise RuntimeError("Could not initialize Anthropic client from library. Ensure 'anthropic' package is installed and up-to-date.")


def build_prompt(item_obj, item_type="pr"):
    if item_type == "commit":
        sha = item_obj.get("sha", "")
        message = ""
        author_name = ""
        commit_data = item_obj.get("commit", {})
        if isinstance(commit_data, dict):
            message = commit_data.get("message", "")
            author = commit_data.get("author", {})
            if isinstance(author, dict):
                author_name = author.get("name", "")
        if not message:
            message = item_obj.get("message", "")

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
        )

        data_section = {
            "sha": sha,
            "message": message,
            "author": author_name,
            "raw": item_obj,
        }
    else:
        title = item_obj.get("title", "")
        description = item_obj.get("body", item_obj.get("description", ""))
        commit_messages = item_obj.get("commit_messages") or item_obj.get("commits") or []
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
        )

        data_section = {
            "title": title,
            "description": description,
            "commit_messages": commit_messages,
            "raw": item_obj,
        }

    prompt = (
        f"{HUMAN_PROMPT}\n{instruction}\nDATA:\n{json.dumps(data_section, indent=2, default=str)}\n"
        f"Please respond with a single JSON object only.\n{AI_PROMPT}\n"
    )
    return prompt


def call_anthropic(client, prompt, model="claude-haiku-4-5", max_tokens=512):
    # Attempt to use common SDK callnames; adapt to installed version
    try:
        # resp = client.messages.create(model=model, prompt=prompt, max_tokens_to_sample=max_tokens)
        resp = client.messages.create(
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model=model,
        )

        text = resp.content[0].text if hasattr(resp, "content") and isinstance(resp.content, list) and len(resp.content) > 0 else None
        if not text:
            text = str(resp)
        return text
    except Exception:
        try:
            print("First attempt to call Anthropic API failed, trying alternative method..." + str(sys.exc_info()))
            # resp = client.messages.create(prompt=prompt, model=model, max_tokens_to_sample=max_tokens)
            resp = client.messages.create(
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=model,
            )
            return resp.content or str(resp)
        except Exception as e:
            raise RuntimeError(f"Anthropic API call failed: {e}")


def determine_item_type(item_obj):
    if isinstance(item_obj, dict):
        if "sha" in item_obj or "commit" in item_obj:
            return "commit"
        if "number" in item_obj and "title" in item_obj:
            return "pr"
    return "pr"


def summarize_items(item_list, client, item_type="pr", model="claude-haiku-4-5", max_tokens=512):
    results = []
    for item in item_list:
        prompt = build_prompt(item, item_type=item_type)
        text = call_anthropic(client, prompt, model=model, max_tokens=max_tokens)

        parsed = None
        try:
            parsed = json.loads(text)
        except Exception:
            import re

            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    parsed = {"raw_response": text}
            else:
                parsed = {"raw_response": text}

        if item_type == "pr":
            parsed.setdefault("pr_number", item.get("number") if isinstance(item, dict) else getattr(item, "number", None))
        else:
            parsed.setdefault("commit_sha", item.get("sha") if isinstance(item, dict) else getattr(item, "sha", None))
        parsed.setdefault("_model_used", model)
        results.append(parsed)
    return results


def load_input(path_or_dash):
    if not path_or_dash or path_or_dash == "-":
        data = sys.stdin.read()
        if not data.strip():
            raise RuntimeError("No input on stdin")
        obj = json.loads(data)
        return obj
    else:
        # print(f"Loading input from {path_or_dash}...")
        with open(path_or_dash, "r", encoding="utf-8") as f:
            # print(f"File content:\n{f.read(500)}\n--- End of file preview ---")
            return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Summarize PR or commit JSON using Anthropic")
    parser.add_argument("input", nargs="?", default="-", help="Path to JSON file with PR or commit object/array. Use - or omit to read stdin.")
    parser.add_argument("--model", default="claude-haiku-4-5", help="Anthropic model to use")
    parser.add_argument("--max-tokens", type=int, default=512, help="Max tokens to request from the model")
    args = parser.parse_args()

    client = load_anthropic_client()

    raw = load_input(args.input)
    if isinstance(raw, dict):
        item_list = [raw]
    elif isinstance(raw, list):
        item_list = raw
    else:
        raise RuntimeError("Input JSON must be an object or array of objects representing PRs or commits")

    item_type = determine_item_type(item_list[0])
    summaries = summarize_items(item_list, client, item_type=item_type, model=args.model, max_tokens=args.max_tokens)

    for s in summaries:
        print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
