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
import { PrepareStockReservationDto } from './dto/prepare-stock-reservation.dto';
import { StockReservationActionDto } from './dto/stock-reservation-action.dto';

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

  @Patch('prepare-reserve')
  @ApiOperation({ summary: 'Prepara reserva de estoque para 2PC' })
  @ApiBody({ type: PrepareStockReservationDto })
  prepareReserve(@Body() dto: PrepareStockReservationDto) {
    return this.warehouseService.prepareStockReservation(dto);
  }

  @Patch('commit-reserve')
  @ApiOperation({ summary: 'Confirma reserva de estoque no 2PC' })
  @ApiBody({ type: StockReservationActionDto })
  commitReserve(@Body() dto: StockReservationActionDto) {
    return this.warehouseService.commitStockReservation(dto);
  }

  @Patch('rollback-reserve')
  @ApiOperation({ summary: 'Cancela reserva de estoque no 2PC' })
  @ApiBody({ type: StockReservationActionDto })
  rollbackReserve(@Body() dto: StockReservationActionDto) {
    return this.warehouseService.rollbackStockReservation(dto);
  }
}
