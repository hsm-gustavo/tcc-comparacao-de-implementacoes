import { ApiProperty } from '@nestjs/swagger';

export class ReserveStockDto {
  @ApiProperty({ example: 'prod-123' })
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  quantity: number;
}
