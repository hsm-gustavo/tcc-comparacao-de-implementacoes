#!/usr/bin/env python3
"""Optional historical fixture. Writes ONLY when explicitly invoked by the isolated runner.
No benchmark, HTTP calls, RabbitMQ events, schema changes or resets are performed.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import uuid

ARCHITECTURES = ('distributed-baseline', 'tcc', 'two-phase-commit', 'orchestrated-saga', 'choreographed-saga')
SERVICES = ('warehouse', 'billing', 'shipping', 'order')


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def tables(architecture, service):
    if service == 'order':
        return ['orders', 'order_items']
    if service == 'billing':
        return ['payments']
    if service == 'shipping':
        return ['shipments']
    return ['products'] + (['stock_reservations'] if architecture in ('tcc', 'two-phase-commit') else
                           ['stock_reservations_saga'] if architecture == 'choreographed-saga' else [])


def catalog_rows(products, count):
    if not 1 <= count <= 10000000:
        raise ValueError('PRELOAD_ORDERS must be between 1 and 10000000')
    if not products:
        raise ValueError('Preload requires an existing product catalog')
    rows, seen = [], set()
    for slot, product in enumerate(sorted(products, key=lambda p: (p['name'], p['id'])), 1):
        product_id = str(uuid.UUID(product['id']))
        if product_id in seen:
            raise ValueError('Duplicate product ID')
        seen.add(product_id)
        price = Decimal(str(product['price']))
        if not price.is_finite() or price <= 0 or price != price.quantize(Decimal('.01')):
            raise ValueError('Product price must be positive with at most two decimals')
        stock = product['stockQuantity']
        if type(stock) is not int or stock < 0:
            raise ValueError('Invalid stock')
        consumed = count // len(products) + (slot <= count % len(products))
        if consumed > stock:
            raise ValueError(f'Insufficient stock for historical orders: {product_id}')
        rows.append({'slot': slot, 'id': product_id, 'price': str(price), 'stock': stock, 'consumed': consumed})
    return rows


def empty_check(names):
    return '\n'.join(f"IF EXISTS (SELECT 1 FROM public.\"{name}\" LIMIT 1) THEN "
                     f"RAISE EXCEPTION 'Preload requires empty {name}; nothing will be deleted'; END IF;"
                     for name in names if name != 'products')


def plan_sql(architecture, service, count, catalog):
    if architecture not in ARCHITECTURES or service not in SERVICES:
        raise ValueError('Unsupported architecture/service')
    names = tables(architecture, service)
    catalog_json = literal(json.dumps(catalog))
    sql = f'''SET lock_timeout = '5s';
BEGIN;
LOCK TABLE {', '.join('public."'+name+'"' for name in names)} IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN {empty_check(names)} END $$;
CREATE TEMP TABLE historical_catalog ON COMMIT DROP AS
SELECT * FROM jsonb_to_recordset({catalog_json}::jsonb)
AS c(slot integer, id uuid, price numeric, stock integer, consumed integer);
CREATE TEMP TABLE historical_orders ON COMMIT DROP AS
SELECT i, md5('pilot-history-order-'||i)::uuid AS order_id,
       md5('pilot-history-item-'||i)::uuid AS item_id, c.id AS product_id, c.price,
       timestamp '2020-01-01' + i * interval '1 second' AS created_at
FROM generate_series(1,{count}) AS g(i)
JOIN historical_catalog c ON c.slot = ((i-1) % {len(catalog)})+1;
'''
    if service == 'order':
        sql += '''INSERT INTO public.orders(id,"customerId","totalAmount",status,"createdAt")
SELECT order_id, 'historical-preload-'||i, price, 'confirmed', created_at FROM historical_orders;
INSERT INTO public.order_items(id,"orderId","productId",quantity)
SELECT item_id, order_id, product_id, 1 FROM historical_orders;
'''
        checks = f'''(SELECT count(*) FROM public.orders)={count}
AND (SELECT count(*) FROM public.order_items)={count}
AND NOT EXISTS(SELECT 1 FROM historical_orders h
 LEFT JOIN public.orders o ON o.id=h.order_id
 LEFT JOIN public.order_items i ON i.id=h.item_id AND i."orderId"=h.order_id
 WHERE o.id IS NULL OR i.id IS NULL OR o.status<>'confirmed' OR o."totalAmount"<>h.price
 OR i.quantity<>1 OR i."productId"<>h.product_id::text)'''
    elif service == 'billing':
        sql += '''INSERT INTO public.payments(id,"orderId",amount,status,"createdAt")
SELECT md5('pilot-history-payment-'||i)::uuid, order_id, price, 'completed', created_at FROM historical_orders;
'''
        checks = f'''(SELECT count(*) FROM public.payments)={count}
AND NOT EXISTS(SELECT 1 FROM historical_orders h LEFT JOIN public.payments p
 ON p.id=md5('pilot-history-payment-'||h.i)::uuid AND p."orderId"=h.order_id::text
 WHERE p.id IS NULL OR p.status<>'completed' OR p.amount<>h.price)'''
    elif service == 'shipping':
        sql += '''INSERT INTO public.shipments(id,"orderId",status,"trackingCode","createdAt")
SELECT md5('pilot-history-shipment-'||i)::uuid, order_id, 'shipped', 'HIST-'||i, created_at FROM historical_orders;
'''
        checks = f'''(SELECT count(*) FROM public.shipments)={count}
AND NOT EXISTS(SELECT 1 FROM historical_orders h LEFT JOIN public.shipments s
 ON s.id=md5('pilot-history-shipment-'||h.i)::uuid AND s."orderId"=h.order_id::text
 WHERE s.id IS NULL OR s.status<>'shipped' OR s."trackingCode" IS NULL)'''
    else:
        sql += '''DO $$ BEGIN
IF (SELECT count(*) FROM public.products)<>(SELECT count(*) FROM historical_catalog)
 OR EXISTS(SELECT 1 FROM historical_catalog c LEFT JOIN public.products p ON p.id=c.id
 WHERE p.id IS NULL OR p."stockQuantity"<>c.stock OR p.price<>c.price)
THEN RAISE EXCEPTION 'Catalog changed before preload'; END IF; END $$;
UPDATE public.products p SET "stockQuantity"=c.stock-c.consumed
FROM historical_catalog c WHERE p.id=c.id;
'''
        if architecture in ('tcc', 'two-phase-commit', 'choreographed-saga'):
            table = names[1]
            state = 'reserved' if architecture == 'choreographed-saga' else 'committed'
            sql += f'''INSERT INTO public.{table}(id,"orderId","productId",quantity,status,"createdAt")
SELECT md5('pilot-history-reservation-'||i)::uuid, order_id, product_id, 1, '{state}', created_at FROM historical_orders;
'''
            extra = f''' AND (SELECT count(*) FROM public.{table})={count}
AND NOT EXISTS(SELECT 1 FROM historical_orders h LEFT JOIN public.{table} r
 ON r.id=md5('pilot-history-reservation-'||h.i)::uuid AND r."orderId"=h.order_id::text
 WHERE r.id IS NULL OR r.status<>'{state}' OR r.quantity<>1 OR r."productId"<>h.product_id::text)'''
        else:
            extra = ''
        checks = '''NOT EXISTS(SELECT 1 FROM public.products p JOIN historical_catalog c ON p.id=c.id
 WHERE p."stockQuantity"<>c.stock-c.consumed OR p."stockQuantity"<0)''' + extra
    sql += f'''DO $$ BEGIN IF NOT ({checks}) THEN RAISE EXCEPTION 'Historical fixture validation failed'; END IF; END $$;
COMMIT;
'''
    sql += '\n'.join(f'ANALYZE public."{name}";' for name in names) + '\n'
    sql += f"SELECT json_build_object('service',{literal(service)},'historicalOrders',{count},'validated',true);\n"
    return sql


def command(compose, service):
    return ['docker', 'compose', '-f', str(compose), 'exec', '-T', '-e',
            'PGOPTIONS=-c application_name=pilot-historical-preload', service+'-db',
            'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'user', '-d', service]


def execute(compose, architecture, count, products, output, timeout=600):
    output.mkdir(parents=True, exist_ok=False)
    catalog = catalog_rows(products, count)
    record = {'architecture': architecture, 'orders': count, 'quantityPerOrder': 1,
              'policy': 'Successful synthetic history; no resets between benchmark repetitions',
              'catalog': catalog, 'services': {}, 'status': 'preflight'}
    manifest = output/'manifest.json'
    def save():
        record['updatedAt'] = datetime.now(timezone.utc).isoformat()
        manifest.write_text(json.dumps(record, indent=2)+'\n')
    save()
    try:
        # Check every database before changing any. Transactions repeat the checks under locks.
        for service in SERVICES:
            checks = empty_check(tables(architecture, service))
            sql = f'DO $$ BEGIN {checks} END $$;'
            result = subprocess.run(command(compose, service), input=sql, capture_output=True,
                                    text=True, timeout=timeout)
            (output/(service+'-preflight.log')).write_text(result.stdout+result.stderr)
            if result.returncode:
                raise RuntimeError(f'{service}: preflight failed; see {output}')
        record['status'] = 'loading'
        save()
        for service in SERVICES:
            sql = plan_sql(architecture, service, count, catalog)
            (output/(service+'.sql')).write_text(sql)
            result = subprocess.run(command(compose, service), input=sql, capture_output=True,
                                    text=True, timeout=timeout)
            (output/(service+'.log')).write_text(result.stdout+result.stderr)
            if result.returncode:
                raise RuntimeError(f'{service}: preload failed; no benchmark may start; see {output}')
            data = json.loads(result.stdout.strip())
            if data != {'service': service, 'historicalOrders': count, 'validated': True}:
                raise RuntimeError('Unexpected preload validation response')
            record['services'][service] = data
            save()
        record['status'] = 'completed'
        save()
        return record
    except Exception as error:
        record['status'] = 'failed'
        record['error'] = str(error)
        save()
        # Cross-database commits are not atomic. Preserve partial state and never auto-reset/retry.
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', type=Path, required=True)
    parser.add_argument('--architecture', choices=ARCHITECTURES, required=True)
    parser.add_argument('--orders', type=int, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    execute(args.compose, args.architecture, args.orders, json.loads(args.catalog.read_text()), args.output)
