Monitor page fixtures (HK-015). These are display fixtures, not valid audit
logs: their hash chains are deliberately not maintained.

- `unknown-and-gap.jsonl`: first 20 sample records with sequence 5 removed,
  two lines swapped, and one unregistered event type (`agent.chatted`).
- `authoritative-verdict.jsonl`: the sample run with an authoritative CI verdict.
