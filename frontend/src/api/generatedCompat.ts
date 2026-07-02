import type {
  BodyLoginLoginAccessToken,
  ItemCreate,
  ItemUpdate,
  NewPassword,
  UpdatePassword,
  UserRegister,
  UserUpdateMe,
} from "@/client"
import {
  itemsCreateItem,
  itemsDeleteItem,
  itemsReadItems,
  itemsUpdateItem,
  loginLoginAccessToken,
  loginRecoverPassword,
  loginResetPassword,
  usersDeleteUserMe,
  usersReadUserMe,
  usersRegisterUser,
  usersUpdatePasswordMe,
  usersUpdateUserMe,
} from "@/client"

export const ItemsService = {
  readItems: async (parameters?: { skip?: number; limit?: number }) =>
    (await itemsReadItems(parameters, { throwOnError: true })).data,
  createItem: async ({ requestBody }: { requestBody: ItemCreate }) =>
    (await itemsCreateItem({ itemCreate: requestBody }, { throwOnError: true }))
      .data,
  updateItem: async ({
    id,
    requestBody,
  }: {
    id: string
    requestBody: ItemUpdate
  }) =>
    (
      await itemsUpdateItem(
        { id, itemUpdate: requestBody },
        { throwOnError: true },
      )
    ).data,
  deleteItem: async ({ id }: { id: string }) =>
    (await itemsDeleteItem({ id }, { throwOnError: true })).data,
}

export const UsersService = {
  readUserMe: async () => (await usersReadUserMe({ throwOnError: true })).data,
  registerUser: async ({ requestBody }: { requestBody: UserRegister }) =>
    (
      await usersRegisterUser(
        { userRegister: requestBody },
        { throwOnError: true },
      )
    ).data,
  updateUserMe: async ({ requestBody }: { requestBody: UserUpdateMe }) =>
    (
      await usersUpdateUserMe(
        { userUpdateMe: requestBody },
        { throwOnError: true },
      )
    ).data,
  updatePasswordMe: async ({ requestBody }: { requestBody: UpdatePassword }) =>
    (
      await usersUpdatePasswordMe(
        { updatePassword: requestBody },
        { throwOnError: true },
      )
    ).data,
  deleteUserMe: async () =>
    (await usersDeleteUserMe({ throwOnError: true })).data,
}

export const LoginService = {
  loginAccessToken: async ({
    formData,
  }: {
    formData: BodyLoginLoginAccessToken
  }) =>
    (
      await loginLoginAccessToken(
        { bodyLoginLoginAccessToken: formData },
        { throwOnError: true },
      )
    ).data,
  recoverPassword: async ({ email }: { email: string }) =>
    (await loginRecoverPassword({ email }, { throwOnError: true })).data,
  resetPassword: async ({ requestBody }: { requestBody: NewPassword }) =>
    (
      await loginResetPassword(
        { newPassword: requestBody },
        { throwOnError: true },
      )
    ).data,
}
