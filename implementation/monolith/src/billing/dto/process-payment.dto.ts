import { ApiProperty } from '@nestjs/swagger';
import { IsNumber, IsString, Min } from 'class-validator';

export class ProcessPaymentDto {
  @ApiProperty({ example: 'order-123' })
  @IsString()
  orderId: string;

  @ApiProperty({ example: 199.9, minimum: 0 })
  @IsNumber()
  @Min(0)
  amount: number;
}
