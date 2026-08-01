import { ApiProperty } from '@nestjs/swagger';
import { IsString } from 'class-validator';

export class RefundPaymentDto {
  @ApiProperty({ example: 'order-123' })
  @IsString()
  orderId: string;
}
