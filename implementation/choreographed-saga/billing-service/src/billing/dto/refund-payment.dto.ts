import { ApiProperty } from '@nestjs/swagger';

export class RefundPaymentDto {
  @ApiProperty({ example: 'order-123' })
  orderId: string;
}
