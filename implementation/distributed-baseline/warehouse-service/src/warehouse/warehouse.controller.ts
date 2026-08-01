import {
  Controller,
  Post,
  Body,
  Patch,
  Get,
  Param,
  Delete,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { WarehouseService } from './warehouse.service';
import { CreateProductDto } from './dto/create-product.dto';
import { ReserveStockDto } from './dto/reserve-stock.dto';

@ApiTags('warehouse')
@Controller('warehouse')
export class WarehouseController {
  constructor(private readonly warehouseService: WarehouseService) {}

  @Post('products')
  @ApiOperation({ summary: 'Cria produto no estoque' })
  @ApiBody({ type: CreateProductDto })
  create(@Body() createProductDto: CreateProductDto) {
    return this.warehouseService.create(createProductDto);
  }

  @Get('products')
  @ApiOperation({ summary: 'Lista produtos' })
  findAll() {
    return this.warehouseService.findAll();
  }

  @Get('products/:id')
  @ApiOperation({ summary: 'Busca produto por id' })
  @ApiParam({ name: 'id', type: String })
  findOne(@Param('id') id: string) {
    return this.warehouseService.findOne(id);
  }

  @Delete('products/:id')
  @ApiOperation({ summary: 'Remove um produto (Teste de Integridade)' })
  @ApiParam({ name: 'id', type: String })
  remove(@Param('id') id: string) {
    return this.warehouseService.remove(id);
  }

  @Patch('reserve')
  @ApiOperation({ summary: 'Reserva estoque de um produto' })
  @ApiBody({ type: ReserveStockDto })
  reserve(@Body() dto: ReserveStockDto) {
    return this.warehouseService.reserveStock(dto);
  }
}
