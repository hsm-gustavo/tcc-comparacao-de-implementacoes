#!/usr/bin/env bash

set -euo pipefail

MODE="${1:-${MODE:-distributed}}"
PRODUCT_COUNT="${PRODUCT_COUNT:-100}"
INITIAL_STOCK="${INITIAL_STOCK:-100000}"

if [[ "$MODE" == "monolith" ]]; then
    BASE_URL="http://127.0.0.1:3000"
    PREFIX=""
elif [[ "$MODE" == "distributed" ]]; then
    BASE_URL="http://127.0.0.1:8000"
    PREFIX="/warehouse"
else
    echo "Error: Mode '$MODE' invalid. Use 'monolith' or 'distributed'."
    exit 1
fi

HEALTH_URL="${BASE_URL}${PREFIX}/health"
CREATE_PRODUCT_URL="${BASE_URL}/warehouse/products"

echo "Waiting Warehouse Service ($HEALTH_URL)..."

until curl -sf "$HEALTH_URL" >/dev/null 2>&1; do
    sleep 1
done

echo "Creating $PRODUCT_COUNT products..."

for index in $(seq 1 "$PRODUCT_COUNT"); do
    price_integer=$((100 + index))
    price_decimal=$((index % 100))

    product=$(cat <<EOF
{
  "name": "Benchmark Product $(printf '%03d' "$index")",
  "price": ${price_integer}.$(printf '%02d' "$price_decimal"),
  "stockQuantity": ${INITIAL_STOCK}
}
EOF
)

    curl \
        --silent \
        --show-error \
        --fail \
        -X POST \
        "$CREATE_PRODUCT_URL" \
        -H "Content-Type: application/json" \
        -d "$product" \
        >/dev/null

    printf "\rCreated products: %d/%d" \
        "$index" \
        "$PRODUCT_COUNT"
done

echo
echo "Seed completed."
