import {
  Column,
  CreateDateColumn,
  Entity,
  PrimaryGeneratedColumn,
} from 'typeorm';

export enum StockReservationStatus {
  RESERVED = 'reserved',
  RELEASED = 'released',
}

@Entity('stock_reservations_saga')
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
    enum: StockReservationStatus,
    default: StockReservationStatus.RESERVED,
  })
  status: StockReservationStatus;

  @CreateDateColumn()
  createdAt: Date;
}
