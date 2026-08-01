import { ApiProperty } from '@nestjs/swagger';

export class CreateProductDto {
  @ApiProperty({ example: 'Notebook Gamer' })
  name: string;

  @ApiProperty({ example: 4999.99, minimum: 0 })
  price: number;

  @ApiProperty({ example: 10, minimum: 0 })
  stockQuantity: number;
}
