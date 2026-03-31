import { SetMetadata } from '@nestjs/common';

/**
 * 元数据键名常量
 *
 * 用于在路由元数据中存储操作类型映射。
 * PermissionGuard（待实现）通过 Reflector 读取此键来进行操作级权限校验。
 */
export const ACTIONS_KEY = 'actions';

/**
 * 操作类型映射接口
 *
 * 定义 Controller 方法与操作类型的对应关系。
 * 每种操作类型对应一组方法名数组。
 */
export interface ActionsMap {
  /** 读取操作对应的方法名列表（如 ['getList', 'getDetail']） */
  read?: string[];
  /** 写入操作对应的方法名列表（如 ['doEdit']） */
  write?: string[];
  /** 删除操作对应的方法名列表（如 ['doDelete']） */
  delete?: string[];
}

/**
 * @Actions() 装饰器
 *
 * 声明 Controller 中各方法对应的操作类型，供 PermissionGuard 进行操作级权限校验。
 * 通常在 Controller 类级别使用。
 *
 * 使用示例：
 * ```typescript
 * @Actions({
 *   read: ['getList', 'getDetail'],
 *   write: ['doEdit'],
 *   delete: ['doDelete'],
 * })
 * @Controller('user')
 * export class UserController extends CurdController { ... }
 * ```
 *
 * @param actions - 操作类型映射对象
 * @returns ClassDecorator & MethodDecorator
 */
export const Actions = (actions: ActionsMap) => SetMetadata(ACTIONS_KEY, actions);
