import { ApiProperty } from '@nestjs/swagger';

export class CreateOrderDto {
  @ApiProperty({ example: 'cust-123' })
  customerId: string;

  @ApiProperty({ example: 'prod-123' })
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  quantity: number;

  @ApiProperty({ example: 199.9, minimum: 0 })
  amount: number;
}
