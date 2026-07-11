"""ProfileBuilder — first-launch onboarding that adapts Hermes to who you are.

Asks a few questions, then pre-builds persona skill packs, seeds memory with your
preferences, and extends the system prompt. Profile lives in .hermes/profile.json.
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PERSONAS: dict[str, dict] = {
    "developer": {
        "label": "Software Developer",
        "addendum": "The user is a software developer. Default to showing code, running tests, and using git. Be terse and technical.",
        "skills": {
            "pack_code_review": (
                "Review a diff for bugs, style, and security",
                [{"tool": "git_diff_preview"}, {"tool": "review_code"}, {"tool": "lint_and_test"}],
            ),
            "pack_tdd_loop": (
                "Red-green-refactor: write failing test, implement, verify",
                [{"tool": "write_file"}, {"tool": "run_command"}, {"tool": "edit_file_line"}, {"tool": "lint_and_test"}],
            ),
            "pack_debug": (
                "Systematic debugging: reproduce, isolate, fix, verify",
                [{"tool": "run_command"}, {"tool": "grep"}, {"tool": "analyze_error"}, {"tool": "edit_file_line"}, {"tool": "lint_and_test"}],
            ),
        },
    },
    "project_manager": {
        "label": "Project Manager",
        "addendum": "The user is a project manager. Lead with summaries, plans, and status. Use the kanban board for tracking; keep technical detail minimal unless asked.",
        "skills": {
            "pack_standup": ("Generate a standup summary from recent git activity and the kanban board", [{"tool": "git_log"}, {"tool": "task_board"}]),
            "pack_plan_breakdown": ("Break a goal into kanban tasks with estimates", [{"tool": "task_board"}]),
            "pack_status_report": ("Compile a stakeholder status report", [{"tool": "git_log"}, {"tool": "task_board"}, {"tool": "recall_memory"}]),
        },
    },
    "doctor_medical": {
        "label": "Doctor / Medical Professional",
        "addendum": "The user is a medical professional. Prioritize accuracy and cite uncertainty explicitly. Never store patient-identifying data in memory. Summarize literature carefully and flag that outputs are not medical advice.",
        "skills": {
            "pack_literature_summary": (
                "Search and summarize research on a clinical topic with citations",
                [{"tool": "web_search"}, {"tool": "web_fetch"}, {"tool": "consult_expert"}],
            ),
            "pack_document_draft": ("Draft clinical documentation or patient education material", [{"tool": "write_file"}, {"tool": "consult_expert"}]),
        },
    },
    "engineer": {
        "label": "Engineer (non-software)",
        "addendum": "The user is an engineer. Show calculations step by step, state units and assumptions, and prefer verifiable numeric answers (use execute_python for math).",
        "skills": {
            "pack_calc_check": ("Perform and double-check an engineering calculation in Python", [{"tool": "execute_python"}, {"tool": "expert_committee"}]),
            "pack_spec_summary": ("Summarize a technical specification or standard", [{"tool": "web_fetch"}, {"tool": "write_file"}]),
        },
    },
    "data_scientist": {
        "label": "Data Scientist / Analyst",
        "addendum": "The user is a data scientist. Prefer pandas/numpy examples, show data-quality checks, and validate results with quick computations.",
        "skills": {
            "pack_eda": ("Exploratory data analysis on a dataset file", [{"tool": "read_file"}, {"tool": "execute_python"}]),
            "pack_train_eval": ("Train and evaluate a quick model with a holdout split", [{"tool": "execute_python"}, {"tool": "write_file"}]),
        },
    },
    "researcher": {
        "label": "Researcher / Academic",
        "addendum": "The user is a researcher. Provide citations, distinguish established results from speculation, and keep a running bibliography in memory.",
        "skills": {
            "pack_lit_review": ("Multi-source literature review with citation list", [{"tool": "web_search"}, {"tool": "web_fetch"}, {"tool": "remember"}]),
            "pack_paper_summary": ("Deep-summarize a paper and extract methodology", [{"tool": "web_fetch"}, {"tool": "write_file"}]),
        },
    },
    "designer": {
        "label": "Designer",
        "addendum": "The user is a designer. Discuss layout, hierarchy, color, and accessibility. Use image analysis on mockups and screenshots when available.",
        "skills": {
            "pack_design_critique": (
                "Critique a UI screenshot for hierarchy, contrast, accessibility",
                [{"tool": "analyze_image"}, {"tool": "consult_expert"}],
            ),
        },
    },
    "writer": {
        "label": "Writer / Content Creator",
        "addendum": "The user is a writer. Prioritize voice, clarity, and structure. Offer alternatives rather than single answers for creative choices.",
        "skills": {
            "pack_draft_edit": ("Draft, then self-edit a piece in two passes", [{"tool": "write_file"}, {"tool": "consult_expert"}]),
        },
    },
    "student": {
        "label": "Student / Learner",
        "addendum": "The user is learning. Explain reasoning step by step, define jargon on first use, and check understanding with short questions.",
        "skills": {
            "pack_explain": ("Explain a concept at three levels of depth", [{"tool": "consult_expert"}]),
        },
    },
    "business": {
        "label": "Business / Founder",
        "addendum": "The user runs a business. Lead with actionable recommendations, costs, and risks. Keep output scannable.",
        "skills": {
            "pack_market_scan": ("Quick market/competitor scan with sources", [{"tool": "web_search"}, {"tool": "web_fetch"}]),
        },
    },
}

QUESTIONS = [
    ("role", "What best describes you? (" + ", ".join(PERSONAS) + ", or describe freely)"),
    ("domains", "What domains/topics will you work on most? (comma-separated)"),
    ("stack", "Main tools/languages you use? (e.g. python, react, excel, none)"),
    ("experience", "Experience level with AI assistants? (beginner/intermediate/expert)"),
    ("goals", "What should Hermes help you achieve first?"),
]


class ProfileBuilder:
    def __init__(
        self,
        profile_path: str = ".hermes/profile.json",
        save_skill_fn: Callable[[str, str, list], None] | None = None,
        memory_store=None,
    ):
        self.profile_path = Path(profile_path)
        self.save_skill_fn = save_skill_fn
        self.memory_store = memory_store

    # ── State ─────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.profile_path.exists()

    def load(self) -> dict | None:
        if not self.exists():
            return None
        try:
            return json.loads(self.profile_path.read_text())
        except ValueError:
            return None

    # ── Interview ─────────────────────────────────────────────
    def interview(self, ask_fn: Callable[[str], str]) -> dict[str, str]:
        answers = {}
        for key, question in QUESTIONS:
            try:
                answers[key] = str(ask_fn(question)).strip()
            except Exception:
                answers[key] = ""
        return answers

    @staticmethod
    def match_persona(role_answer: str) -> str:
        lowered = role_answer.lower().strip()
        if lowered in PERSONAS:
            return lowered
        aliases = {
            "developer": ["dev", "software", "programmer", "coder", "swe", "backend", "frontend", "fullstack"],
            "project_manager": ["pm", "project manager", "product manager", "scrum", "product"],
            "doctor_medical": ["doctor", "physician", "nurse", "medical", "clinician", "md"],
            "engineer": ["engineer", "mechanical", "electrical", "civil", "hardware"],
            "data_scientist": ["data", "analyst", "ml", "machine learning", "statistics"],
            "researcher": ["research", "academic", "phd", "scientist", "professor"],
            "designer": ["design", "ux", "ui", "graphic"],
            "writer": ["writer", "author", "content", "journalist", "blogger"],
            "student": ["student", "learner", "beginner"],
            "business": ["business", "founder", "entrepreneur", "ceo", "manager", "sales", "marketing"],
        }
        for persona, keywords in aliases.items():
            if any(kw in lowered for kw in keywords):
                return persona
        return "developer"

    # ── Build ─────────────────────────────────────────────────
    def build(self, answers: dict[str, str]) -> dict:
        persona_key = self.match_persona(answers.get("role", ""))
        persona = PERSONAS[persona_key]
        profile = {
            "persona": persona_key,
            "persona_label": persona["label"],
            "answers": answers,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        skills_created = []
        if self.save_skill_fn:
            for name, (description, workflow) in persona["skills"].items():
                try:
                    self.save_skill_fn(name, description, workflow)
                    skills_created.append(name)
                except Exception:
                    continue
        if self.memory_store:
            try:
                self.memory_store.add_memory(
                    f"User persona: {persona['label']}. Domains: {answers.get('domains', '?')}. "
                    f"Stack: {answers.get('stack', '?')}. Goals: {answers.get('goals', '?')}",
                    kind="preference",
                    importance=0.9,
                )
                if answers.get("experience"):
                    self.memory_store.add_memory(
                        f"User experience level with AI assistants: {answers['experience']}",
                        kind="preference",
                        importance=0.6,
                    )
            except Exception:
                pass
        profile["skills_created"] = skills_created
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(profile, indent=2))
        return profile

    def system_addendum(self) -> str:
        profile = self.load()
        if not profile:
            return ""
        persona = PERSONAS.get(profile.get("persona", ""))
        base = persona["addendum"] if persona else ""
        goals = profile.get("answers", {}).get("goals", "")
        if goals:
            base += f" Current goal: {goals[:200]}"
        return base.strip()
