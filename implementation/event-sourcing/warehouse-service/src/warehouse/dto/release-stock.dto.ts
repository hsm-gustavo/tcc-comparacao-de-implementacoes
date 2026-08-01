import { ApiProperty } from '@nestjs/swagger';

export class ReleaseStockDto {
  @ApiProperty({ example: 'prod-123' })
  productId: string;

  @ApiProperty({ example: 2, minimum: 1 })
  quantity: number;
}
