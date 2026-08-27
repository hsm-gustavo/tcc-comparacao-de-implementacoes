import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


# dir pros csvs:
# repusers100_run1.csv
# repusers100_run2.csv
# repusers200_run1.csv
# ...
DATASET_DIR = Path(
    "experiments/datasets/monolith"
)

OUTPUT_DIR = DATASET_DIR / "analysis"

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


FILE_PATTERN = re.compile(
    r"repusers(?P<users>\d+)_run(?P<run>\d+)\.csv"
)


def read_run(path: Path) -> dict:
    match = FILE_PATTERN.fullmatch(path.name)

    if not match:
        raise ValueError(
            f"Nome de arquivo inválido: {path.name}"
        )

    users = int(match.group("users"))
    run = int(match.group("run"))

    df = pd.read_csv(
        path,
        low_memory=False,
    )

    requests = df[
        (df["metric_name"] == "http_req_duration")
        & (df["scenario"] == "concurrent_orders")
    ].copy()

    if requests.empty:
        raise ValueError(
            f"Nenhuma requisição encontrada em {path}"
        )

    durations = requests["metric_value"]

    total_requests = len(requests)

    successful_requests = (
        requests["status"] == 201
    ).sum()

    failed_requests = (
        requests["status"] != 201
    ).sum()

    availability = (
        successful_requests
        / total_requests
        * 100
    )

    execution_time_seconds = (
        durations.max() / 1000
    )

    if execution_time_seconds > 0:
        throughput = (
            successful_requests
            / execution_time_seconds
        )
    else:
        throughput = 0

    return {
        "users": users,
        "run": run,

        "total_requests":
            total_requests,

        "successful_requests":
            successful_requests,

        "failed_requests":
            failed_requests,

        "availability_pct":
            availability,

        "latency_mean_ms":
            durations.mean(),

        "latency_median_ms":
            durations.median(),

        "latency_p90_ms":
            durations.quantile(0.90),

        "latency_p95_ms":
            durations.quantile(0.95),

        "latency_max_ms":
            durations.max(),

        "throughput_req_s":
            throughput,
    }


def load_all_runs() -> pd.DataFrame:
    results = []

    for path in sorted(
        DATASET_DIR.glob("repusers*_run*.csv")
    ):
        try:
            result = read_run(path)

            results.append(result)

            print(
                f"Loaded: {path.name}"
            )

        except Exception as error:
            print(
                f"Skipping {path.name}: {error}"
            )

    if not results:
        raise RuntimeError(
            "Nenhum CSV válido encontrado."
        )

    return pd.DataFrame(results)


def aggregate_runs(
    runs: pd.DataFrame,
) -> pd.DataFrame:

    grouped = (
        runs
        .groupby("users")
        .agg(
            runs=(
                "run",
                "count",
            ),

            latency_mean_ms=(
                "latency_mean_ms",
                "mean",
            ),

            latency_std_ms=(
                "latency_mean_ms",
                "std",
            ),

            latency_median_ms=(
                "latency_median_ms",
                "mean",
            ),

            latency_p90_ms=(
                "latency_p90_ms",
                "mean",
            ),

            latency_p95_ms=(
                "latency_p95_ms",
                "mean",
            ),

            latency_max_ms=(
                "latency_max_ms",
                "mean",
            ),

            throughput_req_s=(
                "throughput_req_s",
                "mean",
            ),

            throughput_std=(
                "throughput_req_s",
                "std",
            ),

            availability_pct=(
                "availability_pct",
                "mean",
            ),

            availability_std=(
                "availability_pct",
                "std",
            ),

            successful_requests=(
                "successful_requests",
                "mean",
            ),

            failed_requests=(
                "failed_requests",
                "mean",
            ),
        )
        .reset_index()
        .sort_values("users")
    )

    return grouped


def add_graph_note():
    plt.figtext(
        0.5,
        0.01,
        (
            "Os pontos representam a média das repetições. "
            "O desvio padrão entre as repetições é reportado "
            "na tabela de resultados."
        ),
        ha="center",
        fontsize=8,
    )


def plot_latency(
    summary: pd.DataFrame,
):
    plt.figure(
        figsize=(10, 6)
    )

    plt.plot(
        summary["users"],
        summary["latency_mean_ms"],
        marker="o",
        label="Latência média",
    )

    plt.plot(
        summary["users"],
        summary["latency_p95_ms"],
        marker="o",
        label="P95 médio",
    )

    plt.xlabel(
        "Requisições concorrentes"
    )

    plt.ylabel(
        "Latência (ms)"
    )

    plt.title(
        "Latência por nível de concorrência"
    )

    plt.grid(
        True,
        alpha=0.3,
    )

    plt.legend()

    add_graph_note()

    plt.tight_layout(
        rect=[0, 0.04, 1, 1]
    )

    plt.savefig(
        OUTPUT_DIR / "replatency.png",
        dpi=200,
    )

    plt.close()


def plot_throughput(
    summary: pd.DataFrame,
):
    plt.figure(
        figsize=(10, 6)
    )

    plt.plot(
        summary["users"],
        summary["throughput_req_s"],
        marker="o",
        label="Throughput médio",
    )

    plt.xlabel(
        "Requisições concorrentes"
    )

    plt.ylabel(
        "Throughput útil (req/s)"
    )

    plt.title(
        "Throughput por nível de concorrência"
    )

    plt.grid(
        True,
        alpha=0.3,
    )

    plt.legend()

    add_graph_note()

    plt.tight_layout(
        rect=[0, 0.04, 1, 1]
    )

    plt.savefig(
        OUTPUT_DIR / "repthroughput.png",
        dpi=200,
    )

    plt.close()


def plot_availability(
    summary: pd.DataFrame,
):
    plt.figure(
        figsize=(10, 6)
    )

    plt.plot(
        summary["users"],
        summary["availability_pct"],
        marker="o",
        label="Disponibilidade média",
    )

    plt.xlabel(
        "Requisições concorrentes"
    )

    plt.ylabel(
        "Disponibilidade (%)"
    )

    plt.title(
        "Disponibilidade por nível de concorrência"
    )

    plt.ylim(
        0,
        105,
    )

    plt.grid(
        True,
        alpha=0.3,
    )

    plt.legend()

    add_graph_note()

    plt.tight_layout(
        rect=[0, 0.04, 1, 1]
    )

    plt.savefig(
        OUTPUT_DIR / "repavailability.png",
        dpi=200,
    )

    plt.close()


def main():
    runs = load_all_runs()

    summary = aggregate_runs(
        runs
    )

    runs.to_csv(
        OUTPUT_DIR / "repruns.csv",
        index=False,
    )

    summary.to_csv(
        OUTPUT_DIR / "repsumary.csv",
        index=False,
    )

    plot_latency(summary)
    plot_throughput(summary)
    plot_availability(summary)

    print()
    print(
        "================================"
    )
    print(
        "Resumo"
    )
    print(
        "================================"
    )

    print(
        summary.to_string(
            index=False
        )
    )

    print()
    print(
        f"Resultados salvos em: "
        f"{OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
