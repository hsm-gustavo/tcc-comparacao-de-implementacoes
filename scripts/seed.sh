#!/usr/bin/env bash

set -euo pipefail

WAREHOUSE_URL="${WAREHOUSE_URL:-http://localhost:3002}"

echo "Waiting Warehouse Service..."

until curl -sf "$WAREHOUSE_URL/health" >/dev/null 2>&1; do
    sleep 1
done

echo "Creating products..."

products=(
'{"name":"Teclado Mecânico","price":299.9,"stockQuantity":100000}'
'{"name":"Mouse Sem Fio","price":149.5,"stockQuantity":100000}'
'{"name":"Monitor 24 Polegadas","price":899.0,"stockQuantity":100000}'
'{"name":"Headset Gamer","price":249.9,"stockQuantity":100000}'
'{"name":"Webcam Full HD","price":189.9,"stockQuantity":100000}'
)

for product in "${products[@]}"; do
    curl \
        --silent \
        --show-error \
        --fail \
        -X POST \
        "$WAREHOUSE_URL/warehouse/products" \
        -H "Content-Type: application/json" \
        -d "$product"

    echo
done

echo "Seed completed."
