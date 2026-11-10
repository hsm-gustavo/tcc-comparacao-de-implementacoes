import {
  Column,
  CreateDateColumn,
  Entity,
  Index,
  PrimaryGeneratedColumn,
} from 'typeorm';

export enum ReservationStatus {
  PREPARED = 'prepared',
  COMMITTED = 'committed',
  CANCELLED = 'cancelled',
}

@Entity('stock_reservations')
@Index('IDX_stock_reservations_product_status', ['productId', 'status'])
export class StockReservation {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ unique: true })
  orderId: string;

  @Column()
  productId: string;

  @Column()
  quantity: number;

  @Column({
    type: 'enum',
    enum: ReservationStatus,
    default: ReservationStatus.PREPARED,
  })
  status: ReservationStatus;

  @CreateDateColumn()
  createdAt: Date;
}
