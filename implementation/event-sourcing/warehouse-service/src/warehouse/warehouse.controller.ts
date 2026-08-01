import {
  Controller,
  Post,
  Body,
  Patch,
  Get,
  Param,
  Delete,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { WarehouseService } from './warehouse.service';
import { CreateProductDto } from './dto/create-product.dto';
import { ReserveStockDto } from './dto/reserve-stock.dto';
import { Ctx, EventPattern, Payload, RmqContext } from '@nestjs/microservices';

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

  @Get('products/:id')
  @ApiOperation({ summary: 'Busca produto e reconstrói o saldo atual' })
  @ApiParam({ name: 'id', type: String })
  async findOne(@Param('id') id: string) {
    return this.warehouseService.findOne(id);
  }

  @Patch('reserve')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Reserva estoque via HTTP (Mecanismo síncrono alternativo)',
  })
  @ApiBody({ type: ReserveStockDto })
  async reserve(@Body() dto: ReserveStockDto) {
    return this.warehouseService.reserveStock(
      dto.productId,
      dto.quantity,
      'MANUAL_HTTP',
    );
  }

  @EventPattern('order.placed')
  async handleOrderPlaced(
    @Payload()
    payload: {
      orderId: string;
      productId: string;
      quantity: number;
      amount: number;
    },
    @Ctx() context: RmqContext,
  ) {
    try {
      await this.warehouseService.reserveStock(
        payload.productId,
        payload.quantity,
        payload.orderId,
      );
      context.getChannelRef().ack(context.getMessage());
    } catch (error) {
      context.getChannelRef().nack(context.getMessage(), false, false);
      throw error;
    }
  }

  @Post('replay')
  @ApiOperation({
    summary:
      'Executa o replay dos eventos de estoque aplicando Upsert na tabela de produtos',
  })
  async triggerReplay() {
    return this.warehouseService.replayEvents();
  }
}
