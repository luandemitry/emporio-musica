# Empório da Música — customer support agent

Prototype of a text-message customer support agent for Empório da Música, a fictional
musical instrument store in Campo Grande/MS, built as an AI Engineer technical challenge.

> Work in progress. The full README (setup, technical decisions, known limitations and
> AI-assistant usage) will be finished at the end of the project.

## Language

Code, documentation and commit messages are in English. The agent itself talks to
customers in Brazilian Portuguese, since the store, its data and its policies are Brazilian.

## Data

The files in `data/` come from the challenge. They were only renamed for convenience
(`desafio_tecnico_ai_eng - products.csv` → `products.csv`, `políticas_da_loja.pdf` →
`politicas_da_loja.pdf`); their content is unchanged.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
