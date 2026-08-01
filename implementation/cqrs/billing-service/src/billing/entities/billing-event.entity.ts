import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('billing_events')
export class BillingEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  aggregateId: string;

  @Column()
  type: string;

  @Column('jsonb')
  data: {
    amount?: number;
    reason?: string;
    refundReason?: string;
  };

  @CreateDateColumn()
  createdAt: Date;
}
