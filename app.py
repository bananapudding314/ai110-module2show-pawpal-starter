import re
import streamlit as st

# ---------------------------------------------------------------------------
# Time keyword parser
# ---------------------------------------------------------------------------

# Ordered list of (pattern, hour, minute) — checked top-to-bottom so compound
# phrases are matched before their constituent words.
_TIME_KEYWORDS: list[tuple[str, int, int]] = [
    # compound "before / after X"
    (r"before breakfast",   6, 45),
    (r"after breakfast",    8, 30),
    (r"before lunch",      11, 30),
    (r"after lunch",       13,  0),
    (r"before dinner",     17, 30),
    (r"after dinner",      19, 30),
    # compound descriptors
    (r"early morning",      6,  0),
    (r"mid.?morning",      10,  0),
    (r"late morning",      10, 30),
    (r"mid.?day",          12,  0),
    (r"mid.?afternoon",    14,  0),
    (r"late afternoon",    16,  0),
    (r"late evening",      21,  0),
    (r"late night",        22,  0),
    # single words / short phrases
    (r"\bdawn\b",           6,  0),
    (r"\bsunrise\b",        6, 30),
    (r"\bbreakfast\b",      7, 30),
    (r"\bmorning\b",        8,  0),
    (r"\bbrunch\b",        10, 30),
    (r"\bnoon\b",          12,  0),
    (r"\blunch\b",         12,  0),
    (r"\bafternoon\b",     14,  0),
    (r"\bevening\b",       18,  0),
    (r"\bdinner\b",        18, 30),
    (r"\bsupper\b",        18, 30),
    (r"\bsunset\b",        19,  0),
    (r"\bnight\b",         20,  0),
    (r"\bbedtime\b",       21,  0),
    (r"\bmidnight\b",       0,  0),
]

_EXPLICIT_TIME = re.compile(
    r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)


def parse_time_from_text(text: str) -> tuple[int, int] | None:
    """
    Return (hour, minute) inferred from natural-language constraints,
    or None if no time information is found.
    """
    lower = text.lower()

    # 1. Explicit clock time: "at 3pm", "10:30am", "8 am"
    m = _EXPLICIT_TIME.search(lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        meridiem = m.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return (hour, minute)

    # 2. Keyword phrases
    for pattern, h, mins in _TIME_KEYWORDS:
        if re.search(pattern, lower):
            return (h, mins)

    return None


def describe_inferred_time(hour: int, minute: int) -> str:
    """Return a human-readable label for a parsed time."""
    period = "am" if hour < 12 else "pm"
    display_hour = hour if hour <= 12 else hour - 12
    display_hour = display_hour or 12
    return f"{display_hour}:{minute:02d} {period}"


# ---------------------------------------------------------------------------
# Task class
# ---------------------------------------------------------------------------
class Task:
    def __init__(
        self,
        taskName: str,
        duration: int,
        priority: int,
        preferredHour: int | None = None,
        preferredMinute: int | None = None,
        constraintNote: str = "",
    ):
        self._taskName = taskName
        self._duration = duration          # minutes
        self._priority = priority          # 1=low, 2=medium, 3=high
        self._preferredHour = preferredHour
        self._preferredMinute = preferredMinute
        self._constraintNote = constraintNote

    def changeDuration(self, value: int) -> None:
        self._duration = value

    def changeName(self, name: str) -> None:
        self._taskName = name

    def getTaskName(self) -> str:
        return self._taskName

    def getDuration(self) -> int:
        return self._duration

    def getPriority(self) -> int:
        return self._priority

    def getPreferredTime(self) -> tuple[int, int] | None:
        if self._preferredHour is not None and self._preferredMinute is not None:
            return (self._preferredHour, self._preferredMinute)
        return None

    def getConstraintNote(self) -> str:
        return self._constraintNote

    def __str__(self) -> str:
        priority_label = {1: "low", 2: "medium", 3: "high"}.get(self._priority, str(self._priority))
        pref = (
            f", preferred={self._preferredHour:02d}:{self._preferredMinute:02d}"
            if self._preferredHour is not None
            else ""
        )
        return f"Task(name={self._taskName!r}, duration={self._duration}min, priority={priority_label}{pref})"


# ---------------------------------------------------------------------------
# Schedule class
# ---------------------------------------------------------------------------
class Schedule:
    def __init__(self, startHour: int = 8, startMinute: int = 0):
        self._taskList: list[Task] = []
        self._taskHour: int = startHour
        self._taskMinute: int = startMinute

    def addTask(self, task: Task) -> None:
        self._taskList.append(task)

    def changeHour(self, hour: int, minute: int, taskTitle: str) -> None:
        for task in self._taskList:
            if task.getTaskName() == taskTitle:
                self._taskHour = hour
                self._taskMinute = minute
                break

    def changeMinute(self, hour: int, minute: int, taskTitle: str) -> None:
        for task in self._taskList:
            if task.getTaskName() == taskTitle:
                self._taskHour = hour
                self._taskMinute = minute
                break

    def getTaskHour(self) -> int:
        return self._taskHour

    def getTaskMinute(self) -> int:
        return self._taskMinute

    def buildSchedule(self) -> list[dict]:
        """
        Scheduling logic:
        1. Tasks with a preferred time are anchored at that time (sorted earliest→latest).
        2. Tasks without a preferred time are sorted by priority (high→low) and
           slotted into gaps, or appended at the end of the last anchored task.
        3. If an anchored task would overlap the previous one, it is pushed forward.
        """
        anchored = [t for t in self._taskList if t.getPreferredTime() is not None]
        floating = [t for t in self._taskList if t.getPreferredTime() is None]

        # Sort anchored by preferred time, floating by priority desc
        anchored.sort(key=lambda t: t.getPreferredTime())          # type: ignore[arg-type]
        floating.sort(key=lambda t: t.getPriority(), reverse=True)

        scheduled: list[dict] = []
        cursor = self._taskHour * 60 + self._taskMinute  # current minute-of-day

        PRIORITY_LABEL = {3: "high", 2: "medium", 1: "low"}

        def emit(task: Task, start_min: int) -> int:
            """Append a row and return the minute when this task ends."""
            h, m = divmod(start_min, 60)
            scheduled.append({
                "Task": task.getTaskName(),
                "Start": f"{h:02d}:{m:02d}",
                "Duration (min)": task.getDuration(),
                "Priority": PRIORITY_LABEL.get(task.getPriority(), str(task.getPriority())),
                "Constraint": task.getConstraintNote() or "—",
            })
            return start_min + task.getDuration()

        # Interleave floating tasks into gaps before each anchored task
        for anchored_task in anchored:
            pref_min = anchored_task.getPreferredTime()[0] * 60 + anchored_task.getPreferredTime()[1]  # type: ignore[index]
            pref_min = max(pref_min, cursor)  # never go back in time

            # Fill gap with floating tasks that can fit before this anchor
            remaining_floating = []
            for ft in floating:
                gap = pref_min - cursor
                if gap >= ft.getDuration():
                    cursor = emit(ft, cursor)
                else:
                    remaining_floating.append(ft)
            floating = remaining_floating

            # Emit the anchored task (push forward if cursor already past pref_min)
            cursor = emit(anchored_task, max(pref_min, cursor))

        # Append any leftover floating tasks after all anchored ones
        for ft in floating:
            cursor = emit(ft, cursor)

        return scheduled

    def __str__(self) -> str:
        lines = [f"Schedule starting at {self._taskHour:02d}:{self._taskMinute:02d}"]
        for row in self.buildSchedule():
            lines.append(
                f"  {row['Start']} — {row['Task']} ({row['Duration (min)']} min, {row['Priority']})"
                + (f" [{row['Constraint']}]" if row["Constraint"] != "—" else "")
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")
st.title("🐾 PawPal+")
st.markdown(
    "Welcome to **PawPal+** — a pet care planning assistant that schedules "
    "daily care tasks based on your constraints and priorities."
)

st.divider()

st.subheader("Owner & Pet Info")
owner_name = st.text_input("Owner name", value="Jordan")
pet_name = st.text_input("Pet name", value="Mochi")
species = st.selectbox("Species", ["dog", "cat", "other"])

st.divider()

st.subheader("Add a Task")
st.caption(
    "Describe any timing constraints in plain English — "
    'e.g. "needs to happen after breakfast", "morning only", "before lunch".'
)

if "tasks" not in st.session_state:
    st.session_state.tasks: list[Task] = []

PRIORITY_MAP = {"low": 1, "medium": 2, "high": 3}

col1, col2, col3 = st.columns(3)
with col1:
    task_title = st.text_input("Task title", value="Morning walk")
with col2:
    duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
with col3:
    priority_label = st.selectbox("Priority", ["low", "medium", "high"], index=2)

constraint_text = st.text_input(
    "Constraints / preferences (optional)",
    placeholder='e.g. "must happen in the morning", "after dinner", "at 7pm"',
)

# Live inference preview
inferred_time = parse_time_from_text(constraint_text) if constraint_text.strip() else None
if constraint_text.strip():
    if inferred_time:
        st.info(
            f"Inferred time from your description: **{describe_inferred_time(*inferred_time)}** — "
            "this task will be anchored at that slot."
        )
    else:
        st.warning("No time keyword recognised. This task will be scheduled by priority only.")

if st.button("Add task"):
    ph, pm = (inferred_time if inferred_time else (None, None))
    new_task = Task(
        taskName=task_title,
        duration=int(duration),
        priority=PRIORITY_MAP[priority_label],
        preferredHour=ph,
        preferredMinute=pm,
        constraintNote=constraint_text.strip(),
    )
    st.session_state.tasks.append(new_task)
    st.rerun()

if st.session_state.tasks:
    st.write("**Current tasks:**")
    display_rows = []
    for t in st.session_state.tasks:
        pref = (
            describe_inferred_time(*t.getPreferredTime())
            if t.getPreferredTime()
            else "flexible"
        )
        display_rows.append({
            "Task": t.getTaskName(),
            "Duration (min)": t.getDuration(),
            "Priority": {1: "low", 2: "medium", 3: "high"}[t.getPriority()],
            "Preferred time": pref,
            "Constraint note": t.getConstraintNote() or "—",
        })
    st.table(display_rows)

    if st.button("Clear all tasks"):
        st.session_state.tasks = []
        st.rerun()
else:
    st.info("No tasks yet. Add one above.")

st.divider()

st.subheader("Generate Schedule")
start_hour = st.number_input("Earliest start hour (0–23)", min_value=0, max_value=23, value=7)
start_minute = st.number_input("Earliest start minute (0–59)", min_value=0, max_value=59, value=0)

if st.button("Generate schedule"):
    if not st.session_state.tasks:
        st.warning("Add at least one task before generating a schedule.")
    else:
        schedule = Schedule(startHour=int(start_hour), startMinute=int(start_minute))
        for task in st.session_state.tasks:
            schedule.addTask(task)

        st.success(f"Schedule for **{pet_name}** ({species}), owner: **{owner_name}**")
        st.table(schedule.buildSchedule())

        with st.expander("Raw __str__ output"):
            st.text(str(schedule))
