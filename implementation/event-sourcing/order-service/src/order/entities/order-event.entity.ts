import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('order_events')
export class OrderEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  aggregateId: string;

  @Column()
  type: string;

  @Column('jsonb')
  data: {
    customerId?: string;
    productId?: string;
    quantity?: number;
    amount?: number;
    trackingCode?: string;
  };

  @CreateDateColumn()
  createdAt: Date;
}
