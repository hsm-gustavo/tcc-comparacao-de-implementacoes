import {
  Controller,
  Post,
  Body,
  Patch,
  Get,
  Param,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { CreateProductDto } from './dto/create-product.dto';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { WarehouseQueryService } from './warehouse.query.service';
import { WarehouseCommandService } from './warehouse.command.service';

@ApiTags('warehouse')
@Controller('warehouse')
export class WarehouseController {
  constructor(
    private readonly commandService: WarehouseCommandService,
    private readonly queryService: WarehouseQueryService,
  ) {}

  @Post('products')
  @ApiOperation({ summary: 'Cria produto no estoque' })
  @ApiBody({ type: CreateProductDto })
  create(@Body() createProductDto: CreateProductDto) {
    return this.commandService.createProduct(createProductDto);
  }

  @Get('products/:id')
  @ApiOperation({ summary: 'Busca produto e reconstrói o saldo atual' })
  @ApiParam({ name: 'id', type: String })
  async findOne(@Param('id') id: string) {
    return this.queryService.getProductById(id);
  }

  @Patch('reserve')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Reserva estoque via HTTP (Mecanismo síncrono alternativo)',
  })
  @ApiBody({ type: ReserveStockDto })
  async reserve(@Body() dto: ReserveStockDto) {
    return this.commandService.reserveStock(
      dto.productId,
      dto.quantity,
      'MANUAL_HTTP',
    );
  }
}
