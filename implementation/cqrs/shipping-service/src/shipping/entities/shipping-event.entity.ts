import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('shipping_events')
export class ShippingEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  aggregateId: string;

  @Column()
  type: string;

  @Column('jsonb')
  data: {
    trackingCode?: string;
    reason?: string;
  };

  @CreateDateColumn()
  createdAt: Date;
}
