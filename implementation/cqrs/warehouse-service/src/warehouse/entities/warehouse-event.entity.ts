import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('warehouse_events')
export class WarehouseEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  aggregateId: string;

  @Column()
  type: string;

  @Column('jsonb')
  data: {
    name?: string;
    price?: number;
    quantity?: number;
    orderId?: string;
    reason?: string;
  };

  @CreateDateColumn()
  createdAt: Date;
}
