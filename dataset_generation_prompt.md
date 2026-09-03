You are an expert tutor skilled in guiding students using questioning, hints, and reflection.

Your task is to generate a realistic tutoring conversation between a tutor and a student
solving a problem, following a Socratic method grounded in three tutor "states."

---

## TUTOR STATE DEFINITIONS

Every tutor turn must be internally classified into exactly one of these states before the
tutor replies:

- **Diagnosis** — goal is to elicit the student's current thinking via open-ended,
  non-leading questions that require explaining reasoning, not just giving an answer.
  Example: "How did you approach this?"

- **Support** — goal is to guide the student toward self-correction via minimal,
  non-explicit hints, without revealing the answer.
  Example: "What happens if you divide both sides by 2?"

- **Synthesis** — goal is to make the student articulate and internalize the concept by
  explaining the rule or reasoning in their own words.
  Example: "In your own words, what's the rule being applied here?"

State transitions should feel realistic, not mechanical. Do not march through
Diagnosis → Support → Synthesis in a fixed pattern every turn. A tutor may:
- stay in Support across several turns if the student keeps struggling
- drop back to Diagnosis if the student says something confused or off-track
- move to Synthesis only once the student has actually reached the correct answer

---

## CONVERSATION PARAMETERS (fixed for this conversation)

- socratic_level: <<SOCRATIC_LEVEL>> — <<SOCRATIC_LEVEL_DEF>>
- student_level: <<STUDENT_LEVEL>> — <<STUDENT_LEVEL_DEF>>
- hint_frequency: <<HINT_FREQUENCY>> — <<HINT_FREQUENCY_DEF>>
- mistake_frequency: <<MISTAKE_FREQUENCY>> — <<MISTAKE_FREQUENCY_DEF>>
- verbosity: <<VERBOSITY>> — <<VERBOSITY_DEF>>
- difficulty: <<DIFFICULTY>> — <<DIFFICULTY_DEF>>

These parameters must visibly shape the conversation, not just sit in the config block.
Concretely:

- If socratic_level is **low**: at least half of the tutor's turns must contain a direct
  explanatory statement (not phrased as a question) before or instead of a question.
- If socratic_level is **high**: nearly every tutor turn must be phrased as a question —
  avoid direct explanation even when the student struggles.
- If mistake_frequency is **medium** or **high**: the student must explicitly assert at
  least one factually or procedurally incorrect claim (not merely hedge or express
  uncertainty) before reaching the correct answer. "I'm not sure if..." does not count as
  a mistake — the student must actually state something wrong.
- If mistake_frequency is **low**: the student may express uncertainty but should not
  assert wrong claims.

---

## DOMAIN AND TOPIC

- domain: <<DOMAIN>>
- topic: <<TOPIC>>

Generate ONE problem in this topic at the specified difficulty. Do not solve it for the
student up front.

---

## CONVERSATION REQUIREMENTS

1. Length: 12–18 turns total. Before finalizing your output, count the turns in your
   "conversation" array. If it has fewer than 12, continue the dialogue until it does.
2. Flow: start with confusion or partial understanding, progress toward correct reasoning,
   end with the student explaining the concept in their own words.
3. The student's FIRST turn must show only partial or incomplete reasoning — it must never
   already contain the correct final answer or a fully worked solution. The struggle has to
   be real, not just a formality before the student immediately gets it right.
4. Tutor never gives the full solution outright — guides via the three states above.
5. Student behavior reflects student_level and mistake_frequency: realistic errors, partial
   reasoning, occasional uncertainty — not scripted-sounding.
6. The FINAL tutor turn must be a Synthesis turn, and the student's final turn must contain
   their own explanation of the rule/principle — never the tutor stating it first. The
   conversation must end immediately after that student turn. Do not add any further tutor
   turn (e.g. praise, wrap-up, confirmation) after the student's closing explanation.
7. Write turns as natural, short spoken exchanges — the way a real tutor and student talk
   out loud — not as polished paragraphs or essay-style writing. Match turn length to the
   verbosity parameter, but even at high verbosity, turns should read like speech, not prose.

---

## TUTOR TURN FORMAT (STRICT — applies to every tutor "text" field)

Every tutor turn's "text" value must follow this exact structure, in this exact order:

<reasoning>[1-2 sentences: what the student just demonstrated, what gap or readiness this
reveals, and why that calls for this particular state next]</reasoning><state>Diagnosis|Support|Synthesis</state><response>[the actual message shown to the student]</response>

Reasoning must come before the state tag — the state is the conclusion of the reasoning,
not a label applied after the fact. Student turns are plain text with no tags.

---

## OUTPUT FORMAT — return ONLY valid JSON, nothing else

{
  "config": {
    "socratic_level": "<<SOCRATIC_LEVEL>>",
    "student_level": "<<STUDENT_LEVEL>>",
    "hint_frequency": "<<HINT_FREQUENCY>>",
    "mistake_frequency": "<<MISTAKE_FREQUENCY>>",
    "verbosity": "<<VERBOSITY>>",
    "difficulty": "<<DIFFICULTY>>"
  },
  "domain": "<<DOMAIN>>",
  "topic": "<<TOPIC>>",
  "problem": "<problem statement>",
  "conversation": [
    {"turn": 1, "speaker": "student", "text": "<student message, no tags>"},
    {"turn": 2, "speaker": "tutor", "text": "<reasoning>...</reasoning><state>Diagnosis</state><response>...</response>"}
  ]
}

Do not include any planning, reasoning, or commentary outside this JSON object. Do not wrap
the output in markdown code fences (no ```json). Your response must start with { and end
with } — nothing before, nothing after.

## SELF-CHECK BEFORE OUTPUT — verify every item, fix and re-check if any fail
- Conversation array has between 12 and 18 turns (count them)
- Every tutor "text" field follows <reasoning><state><response> in that order, no exceptions
- State transitions are plausible given mistake_frequency and student_level, not formulaic
- socratic_level requirement is met (see quantified rule above), not just the config label
- If mistake_frequency is medium/high, the student has asserted at least one concretely
  wrong claim, not just hedged
- The student's first turn does not already contain the correct answer
- Tutor never states the rule before the student does
- Final turn pair ends in Synthesis, with the student giving the explanation, and no tutor
  turn follows it
- Output is valid JSON and nothing else — no markdown fences, no preamble, no text after
  the closing brace