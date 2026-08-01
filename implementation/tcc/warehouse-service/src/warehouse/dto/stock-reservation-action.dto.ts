import { ApiProperty } from '@nestjs/swagger';

export class StockReservationActionDto {
  @ApiProperty({ example: 'order-123' })
  orderId: string;
}
