import { ApiProperty } from '@nestjs/swagger';

export class PrepareStockReservationDto {
  @ApiProperty({ example: 'order-123' })
  orderId: string;

  @ApiProperty({ example: 'prod-123' })
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  quantity: number;
}
