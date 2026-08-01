import { ApiProperty } from '@nestjs/swagger';
import { IsString } from 'class-validator';

export class CreateShipmentDto {
  @ApiProperty({ example: 'order-123' })
  @IsString()
  orderId: string;
}
