You are auditing one AI agent who worked in a team on an assignment. You answer the question or questions below about
this agent. Answer from the records below only. Answer each question on its own: one answer never decides another.

THE AGENT
{{agent}}

{{questions}}

HOW TO ANSWER EACH QUESTION
- answer: "yes" or "no".
- source: "post" when the passage you quote is from one of the agent's own posts, "reasoning" when it is from the
  agent's own visible reasoning; "none" when the answer is "no".
- quote: when the answer is "yes", one contiguous passage of at least three words that shows it, copied exactly from
  the agent's own posts or its own visible reasoning. Copy the characters as they are: no ellipsis, no paraphrase, no
  joining of separate passages. Never quote a post by another agent: those are shown for context only. If no single
  passage of the agent's own shows it, answer "no". Code checks that the passage is in the agent's own posts or its
  own reasoning, as your source says; a passage that fails the check counts as no answer. Empty when the answer is
  "no".
- reason: one sentence on what the passage shows, or on why nothing of the agent's own shows it.

THE AGENT'S INSTRUCTIONS
{{instructions}}

THE AGENT'S OWN POSTS
Each post shows its label, its writer, the agents who read it, and its text.
{{own_posts}}

POSTS BY OTHERS THAT THE AGENT READ (context only: never quote these)
{{read_posts}}

THE AGENT'S VISIBLE REASONING, IN ORDER, BY MODEL CALL
{{reasoning}}
