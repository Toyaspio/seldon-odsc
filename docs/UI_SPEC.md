# Research Terminal UI contract

Use a dark research terminal style with a natural-language query field and optional company selection. Show Microsoft, Alphabet, Amazon and Meta as recommendations, while accepting other companies. Display the task status, progress, report mode and limitations visibly. The capital expenditure chart, revenue and cash-flow panels, key claims and source links should render only when matching report records exist. A mock report has empty financial panels and a visible `Mock / unverified` label; do not draw zero-valued bars for absent data.

Show supported, contradicted and insufficient evidence states distinctly. Link each claim to its source IDs and expose source title, publisher, date and quote. If evidence is insufficient, show the limitation. Handle HTTP 404, 422, failed tasks and request timeout with clear messages. Poll task status until a terminal state. Do not infer an API field beyond the OpenAPI and report schema.
