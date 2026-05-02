EXPERIMENTS = {
    "missing_dependency": "Write Python code that uses pandas to read a CSV and print the average price.",
    "file_assumption": "Read users.csv and print all users older than 30.",
    "timeout": "Find the largest prime number by brute force.",
    "standard_library_fallback": 'Parse a JSON string and print the value of "name" without external dependencies.',
    "syntax_simple": "Write a small Python function that prints the first 10 Fibonacci numbers.",
}


def get_experiment(name: str) -> str:
    try:
        return EXPERIMENTS[name]
    except KeyError as exc:
        available = ", ".join(list_experiments())
        raise KeyError(f"unknown experiment '{name}'. Available experiments: {available}") from exc


def list_experiments() -> list[str]:
    return sorted(EXPERIMENTS)
