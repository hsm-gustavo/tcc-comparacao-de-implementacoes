import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { IsNumber, IsOptional, IsString, IsUUID, Min } from 'class-validator';

export class CreateOrderDto {
  @ApiProperty({ example: 'cust-123' })
  @IsString()
  customerId: string;

  @ApiProperty({ example: 'ac40f13e-819b-43d1-b0c2-c3ea555a963c' })
  @IsUUID()
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  @IsNumber()
  @Min(1)
  quantity: number;

  @ApiPropertyOptional({ example: 199.9, minimum: 0 })
  @IsOptional()
  @IsNumber()
  @Min(0)
  amount?: number;
}
