import { ApiProperty } from '@nestjs/swagger';

export class ProcessPaymentDto {
  @ApiProperty({ example: 'order-123' })
  orderId: string;

  @ApiProperty({ example: 199.9, minimum: 0 })
  amount: number;
}
