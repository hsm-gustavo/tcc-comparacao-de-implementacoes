// Deliberately limited TypeORM test double. Unsupported operations fail explicitly.
const { randomUUID } = require('node:crypto');
const { AsyncLocalStorage } = require('node:async_hooks');
const copy = value => structuredClone(value);
class MemoryDatabase {
  constructor(source) {
    this.source = source;
    this.tables = new Map();
    this.indexes = new WeakMap();
    this.context = new AsyncLocalStorage();
    this.tail = Promise.resolve();
    this.manager = {
      getRepository: target => this.repository(target),
      transaction: (...args) => this.transaction(args.at(-1)),
      query: async sql => {
        if (sql !== 'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))')
          throw new Error('Mock database: unsupported SQL: ' + sql);
        if (!this.context.getStore()) throw new Error('Mock lock outside transaction');
        return Promise.resolve([]); // Local transactions already hold the mock mutex.
      },
    };
    for (const method of ['create', 'save', 'find', 'findOne', 'findOneBy', 'update', 'remove'])
      this.manager[method] = (target, ...args) => this.repository(target)[method](...args);
  }
  async exclusive(work) {
    if (this.context.getStore()) return work();
    const previous = this.tail;
    let release;
    this.tail = new Promise(resolve => { release = resolve; });
    await previous;
    try { return await this.context.run({ undo: null }, work); }
    finally { release(); }
  }
  transaction(work) {
    return this.exclusive(async () => {
      const context = this.context.getStore();
      if (context.undo) throw new Error('Mock database: nested transaction unsupported');
      context.undo = [];
      try { return await work(this.manager); }
      catch (error) {
        for (const undo of context.undo.reverse()) undo();
        throw error;
      } finally { context.undo = null; }
    });
  }
  table(target) {
    const meta = this.source.getMetadata(target);
    if (!this.tables.has(meta.name)) this.tables.set(meta.name, new Map());
    return [meta, this.tables.get(meta.name)];
  }
  write(table, id, row) {
    const before = table.get(id);
    const apply = value => {
      for (const [field, index] of this.indexes.get(table) || []) {
        const old = table.get(id)?.[field];
        const ids = index.get(old);
        ids?.delete(id);
        if (ids?.size === 0) index.delete(old);
        if (value !== undefined) {
          if (!index.has(value[field])) index.set(value[field], new Set());
          index.get(value[field]).add(id);
        }
      }
      if (value === undefined) table.delete(id); else table.set(id, copy(value));
    };
    this.context.getStore()?.undo?.push(() => apply(before));
    apply(row);
  }
  repository(target) {
    const db = this;
    const [meta, table] = this.table(target);
    const match = (row, where) => Object.entries(where || {}).every(([key, value]) => {
      if (value && typeof value === 'object') {
        if (value.id) return row[key]?.id === value.id;
        throw new Error('Mock database: unsupported predicate ' + key);
      }
      return row[key] === value;
    });
    const select = where => {
      if (Array.isArray(where)) throw new Error('Mock database: OR unsupported');
      if (where?.id) {
        const row = table.get(where.id);
        return row && match(row, where) ? [row] : [];
      }
      const field = ['orderId', 'productId', 'customerId'].find(key => where?.[key] !== undefined);
      if (field) {
        if (!db.indexes.has(table)) db.indexes.set(table, new Map());
        const indexes = db.indexes.get(table);
        if (!indexes.has(field)) {
          const index = new Map();
          for (const row of table.values()) {
            if (!index.has(row[field])) index.set(row[field], new Set());
            index.get(row[field]).add(row.id);
          }
          indexes.set(field, index);
        }
        return [...(indexes.get(field).get(where[field]) || [])].map(id => table.get(id)).filter(row => match(row, where));
      }
      return [...table.values()].filter(row => match(row, where));
    };
    const repo = {
      manager: this.manager, metadata: meta,
      create: values => Object.assign(new meta.target(), values),
      save: entity => db.exclusive(async () => {
        if (Array.isArray(entity)) return Promise.all(entity.map(row => repo.save(row)));
        const row = copy(entity);
        for (const col of meta.columns) {
          if (row[col.propertyName] === undefined) {
            if (col.isPrimary && col.isGenerated) row[col.propertyName] = randomUUID();
            else if (col.isCreateDate || col.isUpdateDate) row[col.propertyName] = new Date();
            else if (col.default !== undefined) {
              if (typeof col.default === 'function') throw new Error('Mock database: function default unsupported');
              row[col.propertyName] = copy(col.default);
            }
          }
        }
        for (const relation of meta.relations) {
          if (relation.isManyToOne && row[relation.propertyName]) {
            const related = db.table(relation.inverseEntityMetadata.target)[1];
            const id = row[relation.propertyName].id;
            if (!related.has(id)) throw new Error('Mock database: missing relation ' + relation.propertyName);
            row[relation.propertyName] = { id };
          }
        }
        for (const unique of meta.uniques) {
          const fields = unique.columns.map(c => c.propertyName);
          if (fields.every(f => row[f] != null) && [...table.values()].some(other => other.id !== row.id && fields.every(f => other[f] === row[f])))
            throw Object.assign(new Error('Mock unique constraint'), { code: '23505' });
        }
        if (!row.id) throw new Error('Mock database: missing primary id');
        db.write(table, row.id, row);
        Object.assign(entity, copy(row));
        return entity;
      }),
      find: (options = {}) => db.exclusive(() => {
        const result = select(options.where).map(copy);
        for (const relation of meta.relations) {
          const requested = options.relations;
          if (!(Array.isArray(requested) ? requested.includes(relation.propertyName) : requested?.[relation.propertyName])) continue;
          const related = db.table(relation.inverseEntityMetadata.target)[1];
          for (const row of result) {
            row[relation.propertyName] = relation.isOneToMany
              ? [...related.values()].filter(child => child[relation.inverseRelation.propertyName]?.id === row.id).map(copy)
              : copy(related.get(row[relation.propertyName]?.id) || null);
          }
        }
        return result;
      }),
      findOne: async options => (await repo.find(options))[0] || null,
      findOneBy: where => repo.findOne({ where }),
      update: (where, values) => db.exclusive(() => {
        const rows = select(typeof where === 'string' ? { id: where } : where);
        for (const row of rows) db.write(table, row.id, { ...row, ...values });
        return { affected: rows.length, raw: [], generatedMaps: [] };
      }),
      remove: entity => db.exclusive(() => {
        db.write(table, entity.id, undefined);
        for (const relation of meta.relations.filter(r => r.isOneToMany && r.inverseRelation.onDelete === 'CASCADE')) {
          const children = db.table(relation.inverseEntityMetadata.target)[1];
          for (const child of children.values()) if (child[relation.inverseRelation.propertyName]?.id === entity.id) db.write(children, child.id, undefined);
        }
        return entity;
      }),
      createQueryBuilder: () => {
        let values, parameters = {}, conditions = [];
        const builder = {
          update: () => builder,
          set: v => { values = v; return builder; },
          where: (sql, params) => { conditions = [sql]; Object.assign(parameters, params); return builder; },
          andWhere: (sql, params) => { conditions.push(sql); Object.assign(parameters, params); return builder; },
          setParameters: params => { Object.assign(parameters, params); return builder; },
          execute: () => db.exclusive(() => {
            if (conditions[0] !== 'id = :productId' || conditions.some(c => !['id = :productId', '"stockQuantity" >= :quantity'].includes(c)))
              throw new Error('Mock database: unsupported query builder condition');
            if (!values || Object.keys(values).join() !== 'stockQuantity' || typeof values.stockQuantity !== 'function')
              throw new Error('Mock database: unsupported update expression');
            const expression = values.stockQuantity();
            const sign = expression === '"stockQuantity" - :quantity' ? -1 : expression === '"stockQuantity" + :quantity' ? 1 : null;
            if (sign === null) throw new Error('Mock database: unsupported arithmetic');
            const row = table.get(parameters.productId);
            if (!row || (conditions.length > 1 && row.stockQuantity < parameters.quantity)) return { affected: 0 };
            db.write(table, row.id, { ...row, stockQuantity: row.stockQuantity + sign * parameters.quantity });
            return { affected: 1 };
          }),
        };
        return builder;
      },
    };
    return repo;
  }
}
module.exports = { MemoryDatabase };
