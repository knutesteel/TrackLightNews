import json
import ast

def normalize_analysis(data):
    try:
        if not isinstance(data, dict):
            return data
        list_fields = [
            "full_summary_bullets",
            "people_mentioned",
            "prevention_strategies",
            "discovery_questions",
            "organizations_involved",
            "tl_dr",
        ]
        for key in list_fields:
            val = data.get(key)
            if isinstance(val, str):
                s = val.strip()
                parsed = None
                # Try JSON first
                try:
                    parsed = json.loads(s)
                except Exception:
                    try:
                        parsed = ast.literal_eval(s)
                    except Exception:
                        parsed = None
                if isinstance(parsed, (list, dict)):
                    data[key] = parsed
                elif s:
                    data[key] = [s]
                else:
                    data[key] = []
            elif val is None:
                data[key] = []
        if "tl_dr" not in data and "summary" in data:
            data["tl_dr"] = data.get("summary")
        return data
    except Exception:
        return data