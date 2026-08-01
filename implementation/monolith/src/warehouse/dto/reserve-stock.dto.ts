import { ApiProperty } from '@nestjs/swagger';
import { IsNumber, IsUUID, Min } from 'class-validator';

export class ReserveStockDto {
  @ApiProperty({ example: 'ac40f13e-819b-43d1-b0c2-c3ea555a963c' })
  @IsUUID()
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  @IsNumber()
  @Min(1)
  quantity: number;
}
