import { ApiProperty } from '@nestjs/swagger';
import { IsNumber, IsString, Min } from 'class-validator';

export class CreateProductDto {
  @ApiProperty({ example: 'Notebook Gamer' })
  @IsString()
  name: string;

  @ApiProperty({ example: 4999.99, minimum: 0 })
  @IsNumber()
  @Min(0)
  price: number;

  @ApiProperty({ example: 10, minimum: 0 })
  @IsNumber()
  @Min(0)
  stockQuantity: number;
}
